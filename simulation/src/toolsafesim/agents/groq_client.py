"""Groq API client with rate limiting, token bucket, response caching, and budget tracking (Phase 7).

Ensures deterministic execution, zero network requests on cache hits, and strict budget ceilings.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cache import ResponseCache


class BudgetExceededError(RuntimeError):
    """Raised when token or request usage exceeds the designated budget limit."""


class RateLimitExceededError(RuntimeError):
    """Raised when API rate limits are exceeded after maximum retries."""


class TokenBucketRateLimiter:
    """Token-bucket rate limiter enforcing RPM, TPM and minimum inter-request pacing."""

    def __init__(
        self,
        rpm: int = 30,
        tpm: int = 8000,
        pacing_delay: float = 0.0,
        time_func: Callable[[], float] = time.time,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.rpm = rpm
        self.tpm = tpm
        self.pacing_delay = pacing_delay
        self._time = time_func
        self._sleep = sleep_func

        self.last_request_time = 0.0
        self.request_timestamps: list[float] = []
        self.token_records: list[tuple[float, int]] = []

    def acquire(self, estimated_tokens: int = 2000) -> None:
        """Wait if necessary to respect RPM, TPM, and pacing constraints."""
        now = self._time()

        # 1. Enforce minimum pacing delay
        elapsed = now - self.last_request_time
        if self.last_request_time > 0 and elapsed < self.pacing_delay:
            wait_time = self.pacing_delay - elapsed
            self._sleep(wait_time)
            now = self._time()

        # 2. Enforce rolling 60s RPM & TPM
        cutoff = now - 60.0
        self.request_timestamps = [t for t in self.request_timestamps if t > cutoff]
        self.token_records = [rec for rec in self.token_records if rec[0] > cutoff]

        while len(self.request_timestamps) >= self.rpm:
            earliest = self.request_timestamps[0]
            wait_time = max(0.1, earliest + 60.0 - now)
            self._sleep(wait_time)
            now = self._time()
            cutoff = now - 60.0
            self.request_timestamps = [t for t in self.request_timestamps if t > cutoff]

        current_tokens = sum(count for _, count in self.token_records)
        while current_tokens + estimated_tokens > self.tpm and self.token_records:
            earliest = self.token_records[0][0]
            wait_time = max(0.1, earliest + 60.0 - now)
            self._sleep(wait_time)
            now = self._time()
            cutoff = now - 60.0
            self.token_records = [rec for rec in self.token_records if rec[0] > cutoff]
            current_tokens = sum(count for _, count in self.token_records)

        # Record this request
        self.last_request_time = now
        self.request_timestamps.append(now)
        self.token_records.append((now, estimated_tokens))


class UsageLedger:
    """Tracks token spend and API calls with hard budget enforcement."""

    def __init__(self, max_budget_tokens: int = 180000, ledger_file: Path | None = None):
        self.max_budget_tokens = max_budget_tokens
        self.ledger_file = ledger_file
        self.total_requests = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.model_tokens: dict[str, int] = {}
        if ledger_file and Path(ledger_file).is_file():
            try:
                data = json.loads(Path(ledger_file).read_text(encoding="utf-8"))
                self.total_requests = data.get("total_requests", 0)
                self.total_prompt_tokens = data.get("total_prompt_tokens", 0)
                self.total_completion_tokens = data.get("total_completion_tokens", 0)
                self.total_tokens = data.get("total_tokens", 0)
                self.model_tokens = data.get("model_tokens", {})
            except Exception:
                pass

    def check_budget(self, estimated_tokens: int = 2000, model: str | None = None) -> None:
        if model and model in self.model_tokens:
            cur_model = self.model_tokens[model]
            if cur_model + estimated_tokens > self.max_budget_tokens:
                raise BudgetExceededError(
                    f"Budget limit reached for model '{model}': current usage {cur_model} tokens + estimated "
                    f"{estimated_tokens} exceeds ceiling of {self.max_budget_tokens} tokens."
                )
        if self.total_tokens + estimated_tokens > (self.max_budget_tokens * 3):
            raise BudgetExceededError(
                f"Global budget limit reached: current usage {self.total_tokens} tokens exceeds ceiling."
            )

    def record_usage(self, prompt_tokens: int, completion_tokens: int, model: str | None = None) -> None:
        used = prompt_tokens + completion_tokens
        self.total_requests += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_tokens += used
        if model:
            self.model_tokens[model] = self.model_tokens.get(model, 0) + used
        if self.ledger_file:
            self._save_ledger()

    def _save_ledger(self) -> None:
        if not self.ledger_file:
            return
        data = {
            "total_requests": self.total_requests,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "max_budget_tokens": self.max_budget_tokens,
            "model_tokens": self.model_tokens,
        }
        self.ledger_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


class GroqClient:
    """Groq API client integrating cache, rate limiter, retries, and usage tracking."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        cache: ResponseCache | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        ledger: UsageLedger | None = None,
        http_client: Callable[[str, dict[str, str], bytes], tuple[int, dict[str, str], bytes]] | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ):
        self.api_key = api_key or self._discover_api_key()
        self.base_url = base_url.rstrip("/")
        self.cache = cache or ResponseCache()
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(pacing_delay=0.0)
        self.ledger = ledger or UsageLedger()
        self.http_client = http_client
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    @staticmethod
    def _discover_api_key() -> str:
        # Check env var first
        key = os.environ.get("GROQ_API_KEY")
        if key:
            return key.strip()
        # Check api_key.txt in repo simulation root
        p = Path(__file__).resolve().parents[3] / "api_key.txt"
        if p.is_file():
            content = p.read_text(encoding="utf-8").strip()
            if content:
                return content
        return ""

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Request a chat completion with full caching and budget enforcement."""
        # 1. Check disk cache
        cache_key = self.cache.compute_key(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            seed=seed,
        )
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            # Cache hit: 0 tokens charged, 0 network calls
            return cached_result

        # 2. Check budget ceiling
        self.ledger.check_budget(estimated_tokens=2500, model=model)

        # 3. Acquire rate limiter slot
        self.rate_limiter.acquire(estimated_tokens=2500)

        # 4. Construct payload
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = seed
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ToolSafeSim-Agent/0.1",
        }
        url = f"{self.base_url}/chat/completions"

        # 5. Execute with retries & exponential backoff
        response_data = self._execute_request_with_retries(url, headers, payload_bytes)

        # 6. Record usage & persist to cache
        usage = response_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        self.ledger.record_usage(prompt_tokens, completion_tokens, model=model)

        self.cache.set(cache_key, response_data)
        return response_data

    def _execute_request_with_retries(
        self, url: str, headers: dict[str, str], payload_bytes: bytes
    ) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                if self.http_client:
                    status, resp_headers, body = self.http_client(url, headers, payload_bytes)
                else:
                    status, resp_headers, body = self._urllib_post(url, headers, payload_bytes)

                if status == 200:
                    return json.loads(body.decode("utf-8"))

                if status == 429:
                    if attempt >= self.max_retries:
                        raise RateLimitExceededError(f"HTTP 429 Rate Limit exceeded after {self.max_retries} retries: {body.decode('utf-8', errors='replace')}")
                    # Honor Retry-After header if present
                    retry_after = float(resp_headers.get("retry-after", resp_headers.get("Retry-After", "1.0")))
                    wait_time = max(retry_after, self.backoff_factor ** attempt)
                    if wait_time > 30.0:
                        raise RateLimitExceededError(f"API Rate limit Retry-After is {wait_time:.1f}s (exceeds 30s ceiling); triggering fallback.")
                    print(f"    [*] Rate limited by API (HTTP 429). Waiting {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries})...")
                    time.sleep(wait_time)
                    continue

                if 500 <= status < 600:
                    if attempt >= self.max_retries:
                        raise RuntimeError(f"Server error {status}: {body.decode('utf-8', errors='replace')}")
                    time.sleep(self.backoff_factor ** attempt)
                    continue

                # Unrecoverable error (e.g. 400 Bad Request, 401 Unauthorized)
                raise RuntimeError(f"API request failed with status {status}: {body.decode('utf-8', errors='replace')}")

            except urllib.error.HTTPError as e:
                status = e.code
                body = e.read()
                resp_headers = dict(e.headers)
                if status == 429:
                    if attempt >= self.max_retries:
                        raise RateLimitExceededError(f"HTTP 429 Rate Limit exceeded after {self.max_retries} retries: {body.decode('utf-8', errors='replace')}") from e
                    retry_after = float(resp_headers.get("retry-after", "1.0"))
                    wait_time = max(retry_after, self.backoff_factor ** attempt)
                    if wait_time > 30.0:
                        raise RateLimitExceededError(f"API Rate limit Retry-After is {wait_time:.1f}s (exceeds 30s ceiling); triggering fallback.") from e
                    print(f"    [*] Rate limited by API (HTTP 429). Waiting {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries})...")
                    time.sleep(wait_time)
                    continue
                if 500 <= status < 600 and attempt < self.max_retries:
                    time.sleep(self.backoff_factor ** attempt)
                    continue
                raise RuntimeError(f"HTTP Error {status}: {body.decode('utf-8', errors='replace')}") from e

        raise RateLimitExceededError("Failed after maximum retries")

    @staticmethod
    def _urllib_post(url: str, headers: dict[str, str], payload_bytes: bytes) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read()
            return status, resp_headers, body
