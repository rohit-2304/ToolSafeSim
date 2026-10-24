"""Unit tests for Phase 7: LLM Agent Adapter, Groq Client, Cache, Rate Limiter, and Personas.

All tests operate with a fake HTTP client and zero live network dependency.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from toolsafesim.agents import (
    BudgetExceededError,
    GroqClient,
    LLMAgent,
    MalformedAction,
    ResponseCache,
    TokenBucketRateLimiter,
    UsageLedger,
)
from toolsafesim.controller import Controller
from toolsafesim.fixtures.loader import load_pack
from toolsafesim.schemas import (
    AuthorizationPolicy,
    InitialState,
    Scenario,
    ToolCall,
)


def make_mock_openai_response(
    content: str | None = None,
    tool_name: str | None = None,
    tool_arguments: dict[str, Any] | str | None = None,
    prompt_tokens: int = 150,
    completion_tokens: int = 30,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant"}
    if tool_name:
        args_str = tool_arguments if isinstance(tool_arguments, str) else json.dumps(tool_arguments or {})
        message["tool_calls"] = [
            {
                "id": "call_mock_1",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": args_str,
                },
            }
        ]
        message["content"] = None
    else:
        message["content"] = content or "Task completed."

    return {
        "id": "chatcmpl_mock_123",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def test_response_cache_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ResponseCache(tmpdir)
        key = cache.compute_key("test_model", [{"role": "user", "content": "hi"}], seed=42)
        
        assert not cache.has(key)
        assert cache.get(key) is None

        payload = {"data": "ok", "usage": {"total_tokens": 100}}
        cache.set(key, payload)

        assert cache.has(key)
        cached = cache.get(key)
        assert cached == payload

        cleared = cache.clear()
        assert cleared == 1
        assert not cache.has(key)


def test_groq_client_cache_hit_zero_network():
    call_count = 0

    def fake_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        nonlocal call_count
        call_count += 1
        resp = make_mock_openai_response(content="Hello from fake network")
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ResponseCache(tmpdir)
        ledger = UsageLedger(max_budget_tokens=100000)
        client = GroqClient(api_key="mock_key", cache=cache, ledger=ledger, http_client=fake_http)

        # 1. First call: hits fake network
        r1 = client.chat_completion(model="test_model", messages=[{"role": "user", "content": "hello"}])
        assert call_count == 1
        assert r1["choices"][0]["message"]["content"] == "Hello from fake network"
        assert ledger.total_tokens == 180

        # 2. Second identical call: hits disk cache (ZERO network call, ZERO tokens billed to ledger)
        r2 = client.chat_completion(model="test_model", messages=[{"role": "user", "content": "hello"}])
        assert call_count == 1  # Network call count DID NOT increase!
        assert r2 == r1
        assert ledger.total_tokens == 180  # Tokens did NOT increase!


def test_token_bucket_pacing():
    simulated_time = 100.0
    sleeps: list[float] = []

    def mock_time():
        return simulated_time

    def mock_sleep(seconds: float):
        nonlocal simulated_time
        sleeps.append(seconds)
        simulated_time += seconds

    limiter = TokenBucketRateLimiter(
        rpm=30,
        tpm=8000,
        pacing_delay=10.0,
        time_func=mock_time,
        sleep_func=mock_sleep,
    )

    # First call: immediate
    limiter.acquire(1000)
    assert len(sleeps) == 0

    # Advance time by 3 seconds (less than 10.0 pacing delay)
    simulated_time += 3.0

    # Second call: must sleep for remaining 7.0 seconds
    limiter.acquire(1000)
    assert len(sleeps) == 1
    assert pytest.approx(sleeps[0]) == 7.0


def test_groq_client_retry_and_backoff():
    attempts = 0

    def fake_http_retry(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            # Return HTTP 429 Rate Limit with Retry-After header
            return 429, {"retry-after": "0.01"}, b'{"error": "rate limit exceeded"}'
        # Success on 3rd attempt
        resp = make_mock_openai_response(content="Recovered after retries")
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        client = GroqClient(
            api_key="mock_key",
            cache=ResponseCache(tmpdir),
            http_client=fake_http_retry,
            max_retries=3,
            backoff_factor=1.1,
        )
        res = client.chat_completion(model="test_model", messages=[{"role": "user", "content": "ping"}])
        assert attempts == 3
        assert res["choices"][0]["message"]["content"] == "Recovered after retries"


def test_groq_client_budget_stop():
    def fake_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        resp = make_mock_openai_response(content="ok", prompt_tokens=2000, completion_tokens=500)
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Budget is 3000 tokens
        ledger = UsageLedger(max_budget_tokens=3000)
        client = GroqClient(api_key="mock_key", cache=ResponseCache(tmpdir), ledger=ledger, http_client=fake_http)

        # Call 1: consumes 2500 tokens (success)
        client.chat_completion(model="test_model", messages=[{"role": "user", "content": "1"}])
        assert ledger.total_tokens == 2500

        # Call 2: estimated at 2500 tokens, exceeds 3000 ceiling -> must raise BudgetExceededError
        with pytest.raises(BudgetExceededError) as exc_info:
            client.chat_completion(model="test_model", messages=[{"role": "user", "content": "2"}])
        assert "Budget limit reached" in str(exc_info.value)


def test_llm_agent_openai_tool_call_parsing():
    def fake_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        resp = make_mock_openai_response(
            tool_name="search_emails",
            tool_arguments={"query": "invoice", "folder": "inbox"},
        )
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        client = GroqClient(api_key="mock_key", cache=ResponseCache(tmpdir), http_client=fake_http)
        agent = LLMAgent(client=client, model="openai/gpt-oss-120b", persona="careful")
        
        from toolsafesim.agents import AgentView
        view = AgentView(
            user_goal="Find invoice emails",
            history=[],
            available_tools=[{"name": "search_emails", "description": "Search inbox", "parameters": {}}],
        )
        action = agent.next_action(view)

        assert isinstance(action, ToolCall)
        assert action.name == "search_emails"
        assert action.arguments == {"query": "invoice", "folder": "inbox"}


def test_llm_agent_final_answer_parsing():
    def fake_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        resp = make_mock_openai_response(content="All done. No actions needed.")
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        client = GroqClient(api_key="mock_key", cache=ResponseCache(tmpdir), http_client=fake_http)
        agent = LLMAgent(client=client, model="openai/gpt-oss-120b", persona="hurried")

        from toolsafesim.agents import AgentView, FinalAnswer
        view = AgentView(
            user_goal="Nothing to do",
            history=[],
            available_tools=[],
        )
        action = agent.next_action(view)

        assert isinstance(action, FinalAnswer)
        assert action.content == "All done. No actions needed."


def test_llm_agent_malformed_json_handling():
    def fake_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        # Return broken JSON for arguments
        resp = make_mock_openai_response(
            tool_name="search_emails",
            tool_arguments='{"query": "unclosed_string...',
        )
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        client = GroqClient(api_key="mock_key", cache=ResponseCache(tmpdir), http_client=fake_http)
        agent = LLMAgent(client=client)

        from toolsafesim.agents import AgentView
        view = AgentView(user_goal="test", history=[], available_tools=[])
        action = agent.next_action(view)

        assert isinstance(action, MalformedAction)
        assert "unclosed_string" in action.raw_content
        assert "Failed to parse tool call JSON arguments" in action.error_message


def test_llm_agent_personas_and_metadata():
    personas = ["careful", "hurried", "literal", "over_helpful"]
    hashes = set()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ResponseCache(tmpdir)
        client = GroqClient(api_key="mock_key", cache=cache)

        for p in personas:
            agent = LLMAgent(client=client, persona=p, model="qwen/qwen3.8-27b", seed=10)
            meta = agent.meta()
            assert meta["persona"] == p
            assert meta["generator_model"] == "qwen/qwen3.8-27b"
            assert meta["seed"] == 10
            assert len(meta["system_prompt_hash"]) == 64
            hashes.add(meta["system_prompt_hash"])

    # All 4 personas have distinct system prompts and hashes
    assert len(hashes) == 4


def test_controller_end_to_end_with_llm_agent():
    """Verify Controller executes stepping loop with LLMAgent, logging metadata and tool calls."""
    turns = [
        make_mock_openai_response(tool_name="search_emails", tool_arguments={"query": "invoice"}),
        make_mock_openai_response(content="Found invoice email em_invoice_00 and reviewed it."),
    ]
    turn_idx = 0

    def fake_step_http(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        nonlocal turn_idx
        resp = turns[min(turn_idx, len(turns) - 1)]
        turn_idx += 1
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = ResponseCache(tmpdir)
        client = GroqClient(api_key="mock_key", cache=cache, http_client=fake_step_http)
        agent = LLMAgent(client=client, model="openai/gpt-oss-120b", persona="careful", seed=100)

        state = load_pack("productivity_demo_v1")
        sc = Scenario(
            scenario_id="sc_llm_01",
            scenario_family_id="P1",
            category="productivity",
            seed=100,
            user_goal="Find invoices",
            initial_state=InitialState(fixture_pack="productivity_demo_v1"),
            authorization=AuthorizationPolicy(allowed_actions=["search_emails"]),
        )

        log_path = Path(tmpdir) / "episode.jsonl"
        ctrl = Controller(sc, state, agent, log_path=log_path)
        res = ctrl.run()

        assert res.completed is True
        assert res.terminated_by_policy is False
        assert res.steps_taken == 2
        assert res.final_answer == "Found invoice email em_invoice_00 and reviewed it."

        # Verify episode_start recorded LLMAgent metadata
        start_event = res.events[0]
        assert start_event.type == "episode_start"
        assert start_event.payload["generator_model"] == "openai/gpt-oss-120b"
        assert start_event.payload["persona"] == "careful"
        assert start_event.payload["seed"] == 100
        assert "system_prompt_hash" in start_event.payload
