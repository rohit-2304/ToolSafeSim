"""Deterministic on-disk response cache for LLM completions (Phase 7).

Keyed by SHA-256 hash of (model, messages, tools, temperature, seed).
Ensures zero-network, zero-token cost for replayed queries and cached episodes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"


class ResponseCache:
    """Thread-safe and process-safe on-disk key-value cache for LLM responses."""

    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> str:
        """Compute stable SHA-256 hash over canonical request parameters."""
        canonical_obj = {
            "model": model,
            "messages": messages,
            "tools": tools or [],
            "temperature": round(temperature, 4),
            "seed": seed,
        }
        serialized = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _path_for_key(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve cached response object or None if not cached."""
        p = self._path_for_key(key)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, response: dict[str, Any]) -> None:
        """Atomically persist response object under the given key."""
        target = self._path_for_key(key)
        serialized = json.dumps(response, sort_keys=True, indent=2, ensure_ascii=False)
        
        # Atomic write via temp file in same directory
        temp_file = target.with_suffix(".tmp")
        try:
            temp_file.write_text(serialized, encoding="utf-8")
            temp_file.replace(target)
        except OSError:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

    def has(self, key: str) -> bool:
        """Check if key exists in cache."""
        return self._path_for_key(key).is_file()

    def clear(self) -> int:
        """Clear all cached entries and return count of deleted files."""
        count = 0
        for p in self.cache_dir.glob("*.json"):
            p.unlink(missing_ok=True)
            count += 1
        return count
