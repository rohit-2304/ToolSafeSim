"""Live 10-episode smoke test for Phase 7 (Groq LLM Agent Adapter).

Executes 10 real episodes across different scenario families, models, and personas.
Demonstrates rate-limiter pacing, live tool-calling, reference policy evaluation,
and append-only JSONL event logging.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from toolsafesim.agents import GroqClient, LLMAgent, ResponseCache, UsageLedger
from toolsafesim.controller import Controller
from toolsafesim.scenarios.factory import ScenarioFactory

SMOKE_TEST_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs" / "smoke_test"


def run_smoke_test(
    max_budget_tokens: int = 50000,
    pacing_delay: float = 12.0,
    output_dir: Path = SMOKE_TEST_RUNS_DIR,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"
    ledger_file = output_dir / "ledger.json"

    cache = ResponseCache(cache_dir)
    ledger = UsageLedger(max_budget_tokens=max_budget_tokens, ledger_file=ledger_file)
    client = GroqClient(cache=cache, ledger=ledger)

    if not client.api_key:
        print("ERROR: No Groq API key found in env GROQ_API_KEY or api_key.txt", file=sys.stderr)
        return 1

    # Configure pacing on the rate limiter
    client.rate_limiter.pacing_delay = pacing_delay

    factory = ScenarioFactory()

    # 10 diverse configurations across all 10 scenario families, 3 models, and 4 personas
    smoke_configs = [
        ("P1", "openai/gpt-oss-120b", "careful", 101),
        ("P2", "openai/gpt-oss-20b", "hurried", 102),
        ("P3", "qwen/qwen3.8-27b", "literal", 103),
        ("D1", "openai/gpt-oss-120b", "over_helpful", 104),
        ("D2", "openai/gpt-oss-20b", "careful", 105),
        ("C1", "qwen/qwen3.8-27b", "hurried", 106),
        ("W1", "openai/gpt-oss-120b", "literal", 107),
        ("F1", "openai/gpt-oss-20b", "over_helpful", 108),
        ("X1", "qwen/qwen3.8-27b", "careful", 109),
        ("X2", "openai/gpt-oss-120b", "hurried", 110),
    ]

    print("================================================================================")
    print("TOOLSAFESIM PHASE 7: 10-EPISODE LIVE SMOKE TEST")
    print(f"API Key: {client.api_key[:6]}...{client.api_key[-4:]}")
    print(f"Budget ceiling: {max_budget_tokens} tokens | Pacing: {pacing_delay}s between calls")
    print(f"Output directory: {output_dir}")
    print("================================================================================\n")

    start_time = time.time()
    results = []

    for i, (family_id, model, persona, seed) in enumerate(smoke_configs, start=1):
        print(f"[{i:02d}/10] Scenario {family_id} | Model: {model} | Persona: {persona} | Seed: {seed}")
        scenario, state, _, _ = factory.create_scenario(family_id, seed=seed)
        
        agent = LLMAgent(
            client=client,
            model=model,
            persona=persona,
            seed=seed,
            temperature=0.0,
        )

        log_path = output_dir / f"episode_{i:02d}_{family_id}.jsonl"
        ctrl = Controller(scenario, state, agent, log_path=log_path)

        ep_start = time.time()
        try:
            res = ctrl.run()
            duration = time.time() - ep_start
            
            status_desc = "BLOCKED BY POLICY" if res.terminated_by_policy else "COMPLETED"
            if res.final_decision and res.terminated_by_policy:
                status_desc += f" ({res.final_decision.reason_code.value})"
            
            print(f"       -> {status_desc} in {res.steps_taken} steps ({duration:.1f}s)")
            if res.final_answer:
                print(f"       -> Final Answer: {res.final_answer[:90]}...")

            results.append({
                "episode": i,
                "family": family_id,
                "model": model,
                "persona": persona,
                "steps": res.steps_taken,
                "completed": res.completed,
                "terminated_by_policy": res.terminated_by_policy,
                "final_reason": res.final_decision.reason_code.value if res.final_decision else None,
                "duration_seconds": round(duration, 2),
            })
        except Exception as e:  # noqa: BLE001
            print(f"       -> ERROR in episode: {e}", file=sys.stderr)
            results.append({"episode": i, "family": family_id, "error": str(e)})

    total_duration = time.time() - start_time
    summary_path = output_dir / "smoke_test_summary.json"
    summary_data = {
        "total_duration_seconds": round(total_duration, 2),
        "total_tokens_used": ledger.total_tokens,
        "total_requests": ledger.total_requests,
        "episodes": results,
    }
    summary_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

    print("\n================================================================================")
    print("SMOKE TEST COMPLETE!")
    print(f"Total time: {total_duration:.1f}s")
    print(f"Tokens consumed: {ledger.total_tokens:,} / {max_budget_tokens:,} budgeted")
    print(f"API calls made: {ledger.total_requests}")
    print(f"Summary written to: {summary_path}")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())
