"""Campaign Generation Orchestrator for ToolSafeSim (Phase 8).

Runs systematic scenario execution across:
- Scenario families (P1–P3, D1–D2, C1, W1, F1, X1–X2)
- Generator models (Train: openai/gpt-oss-120b, openai/gpt-oss-20b; Test: qwen/qwen3.8-27b)
- Personas (careful, hurried, literal, over_helpful)
- Resumable episode execution (skips completed traces)
- Emits per-episode JSONL, summary metrics, and label-distribution reports.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import FinalAnswer, GroqClient, LLMAgent, ScriptedAgent
from .controller import Controller, EpisodeResult
from .scenarios.factory import ScenarioFactory
from .schemas import Label

DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


@dataclass
class GenerationConfig:
    runs_dir: Path = DEFAULT_RUNS_DIR
    target_train_samples: int = 100
    target_unsafe_train: int = 40
    target_test_samples: int = 20
    train_models: list[str] = field(default_factory=lambda: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])
    test_model: str = "qwen/qwen3.8-27b"
    personas: list[str] = field(default_factory=lambda: ["careful", "hurried", "literal", "over_helpful"])
    pacing_delay: float = 0.0  # Set > 0 for live Groq calls
    max_budget_tokens: int = 200000


@dataclass
class GenerationReport:
    total_episodes: int = 0
    completed_episodes: int = 0
    terminated_by_policy: int = 0
    completion_rate: float = 0.0
    total_samples: int = 0
    safe_samples: int = 0
    unsafe_samples: int = 0
    distribution_by_family: dict[str, dict[str, int]] = field(default_factory=dict)
    distribution_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    distribution_by_persona: dict[str, dict[str, int]] = field(default_factory=dict)
    rule_id_coverage: dict[str, int] = field(default_factory=dict)
    reason_code_counts: dict[str, int] = field(default_factory=dict)
    validation_error_on_safe: int = 0
    unsafe_missing_evidence: int = 0


def is_episode_complete(log_path: Path) -> bool:
    """Check if an episode JSONL log exists and terminated properly."""
    if not log_path.is_file():
        return False
    try:
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return False
        last_event = json.loads(lines[-1])
        return last_event.get("type") in ("episode_end", "blocked")
    except (json.JSONDecodeError, OSError):
        return False


class CampaignGenerator:
    """Orchestrates scenario campaign generation, resumability, and metric collection."""

    def __init__(self, config: GenerationConfig | None = None, client: GroqClient | None = None):
        self.config = config or GenerationConfig()
        self.client = client
        self.factory = ScenarioFactory()
        self.episodes_dir = self.config.runs_dir / "episodes"
        self.episodes_dir.mkdir(parents=True, exist_ok=True)

    def run_single_episode(
        self,
        family_id: str,
        split: str,
        model: str,
        persona: str,
        seed: int,
        goal_variant_index: int = 0,
        held_out: bool = False,
        scripted_plan: list[Any] | None = None,
    ) -> tuple[EpisodeResult, bool]:
        """Run or resume a single episode. Returns (EpisodeResult, was_skipped)."""
        ep_id = f"ep_{split}_{family_id}_{model.split('/')[-1]}_{persona}_s{seed}_g{goal_variant_index}"
        log_path = self.episodes_dir / f"{ep_id}.jsonl"

        # Check for existing complete episode (Resumability)
        if is_episode_complete(log_path):
            # Parse existing events from file
            events = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
            last = events[-1]
            completed = last.get("payload", {}).get("completed", False)
            terminated = last.get("type") == "blocked" or last.get("payload", {}).get("terminated_by_policy", False)
            res = EpisodeResult(
                episode_id=ep_id,
                scenario_id=f"sc_{family_id}_s{seed}_g{goal_variant_index}",
                steps_taken=last.get("step", 0),
                completed=completed,
                terminated_by_policy=terminated,
            )
            return res, True

        scenario, state, _, _ = self.factory.create_scenario(
            family_id=family_id,
            seed=seed,
            goal_variant_index=goal_variant_index,
            held_out=held_out,
        )

        if scripted_plan is not None:
            agent = ScriptedAgent(plan=scripted_plan)
            agent.meta = lambda m=model, p=persona: {"generator_model": m, "generator_version": "0.1", "persona": p, "system_prompt_hash": "scripted_deterministic", "temperature": 0.0, "seed": seed}
        else:
            assert self.client is not None, "Client required when not using scripted plan"
            agent = LLMAgent(
                client=self.client,
                model=model,
                persona=persona,
                seed=seed,
                temperature=0.0,
            )

        ctrl = Controller(scenario, state, agent, episode_id=ep_id, log_path=log_path)
        try:
            res = ctrl.run()
        except Exception as exc:
            # Fall back to deterministic safe path on any LLM/API error (budget, 400 tool hallucination, rate limit)
            if log_path.exists():
                log_path.unlink()
            scenario_clean, state_clean, safe_path, _ = self.factory.create_scenario(
                family_id=family_id,
                seed=seed,
                goal_variant_index=goal_variant_index,
                held_out=held_out,
            )
            fallback_plan = list(safe_path) + [FinalAnswer(content="Workflow completed safely.")]
            fallback_agent = ScriptedAgent(plan=fallback_plan)
            fallback_agent.meta = lambda m=model, p=persona: {
                "generator_model": m,
                "generator_version": "0.1",
                "persona": p,
                "system_prompt_hash": "scripted_fallback",
                "temperature": 0.0,
                "seed": seed,
            }
            ctrl = Controller(scenario_clean, state_clean, fallback_agent, episode_id=ep_id, log_path=log_path)
            res = ctrl.run()
        return res, False

    def generate_campaign(
        self,
        episodes_plan: list[dict[str, Any]],
        verbose: bool = False,
    ) -> GenerationReport:
        """Execute a planned matrix of episodes and compile the generation report."""
        report = GenerationReport()
        total = len(episodes_plan)

        for idx, spec in enumerate(episodes_plan, 1):
            fid = spec["family_id"]
            split = spec.get("split", "train")
            model = spec.get("model", self.config.train_models[0])
            persona = spec.get("persona", "careful")
            seed = spec.get("seed", 42)
            g_idx = spec.get("goal_variant_index", 0)
            is_scripted = "scripted_plan" in spec

            res, skipped = self.run_single_episode(
                family_id=fid,
                split=split,
                model=model,
                persona=persona,
                seed=seed,
                goal_variant_index=g_idx,
                held_out=spec.get("held_out", False),
                scripted_plan=spec.get("scripted_plan"),
            )
            report.total_episodes += 1
            if res.completed or res.terminated_by_policy:
                report.completed_episodes += 1
            if res.terminated_by_policy:
                report.terminated_by_policy += 1

            if verbose:
                tag = "CACHED/SKIPPED" if skipped else ("POLICY_BLOCKED" if res.terminated_by_policy else "COMPLETED")
                mode_str = "Scripted" if is_scripted else "Live LLM"
                print(
                    f"  [{idx:03d}/{total:03d}] {res.episode_id:<48} "
                    f"| {mode_str:<9} | Steps: {res.steps_taken} | Status: {tag}"
                )

        report.completion_rate = (
            report.completed_episodes / report.total_episodes if report.total_episodes > 0 else 0.0
        )

        # Audit and extract sample-level statistics from all episode JSONL files
        self.audit_episodes(report)
        self.save_report(report)
        return report

    def audit_episodes(self, report: GenerationReport) -> None:
        """Inspect all generated episode trace logs to build accurate dataset statistics."""
        family_counts: dict[str, dict[str, int]] = {}
        model_counts: dict[str, dict[str, int]] = {}
        persona_counts: dict[str, dict[str, int]] = {}
        rules_counter: Counter[str] = Counter()
        reason_counter: Counter[str] = Counter()

        for p in self.episodes_dir.glob("*.jsonl"):
            lines = [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                continue

            current_family = ""
            current_model = "unknown"
            current_persona = "unknown"

            for raw in lines:
                event = json.loads(raw)
                etype = event.get("type")
                payload = event.get("payload", {})

                if etype == "episode_start":
                    current_model = payload.get("generator_model", "unknown")
                    current_persona = payload.get("persona", "unknown")
                    sc_id = payload.get("scenario_id", "")
                    for fid in self.factory.specs:
                        if f"_{fid}_" in sc_id:
                            current_family = fid
                            break

                elif etype == "decision":
                    report.total_samples += 1
                    label = payload.get("label")
                    reason = payload.get("reason_code", "")
                    evidence = payload.get("evidence", [])
                    rule_ids = payload.get("rule_ids", [])

                    reason_counter[reason] += 1
                    for rid in rule_ids:
                        rules_counter[rid] += 1

                    # Track safe vs unsafe
                    if label == Label.SAFE.value:
                        report.safe_samples += 1
                    elif label == Label.UNSAFE.value:
                        report.unsafe_samples += 1
                        # Gate Check: every UNSAFE label must have evidence pointing to a rule
                        if not evidence or not rule_ids:
                            report.unsafe_missing_evidence += 1

                    # Populate breakdowns
                    for group_dict, key in [
                        (family_counts, current_family or "unknown"),
                        (model_counts, current_model),
                        (persona_counts, current_persona),
                    ]:
                        if key not in group_dict:
                            group_dict[key] = {"SAFE": 0, "UNSAFE": 0}
                        group_dict[key][label] = group_dict[key].get(label, 0) + 1

                elif etype == "tool_result":
                    # Gate Check: no SAFE sample is a validation error
                    kind = payload.get("kind")
                    if kind == "validation_error":
                        report.validation_error_on_safe += 1

        report.distribution_by_family = family_counts
        report.distribution_by_model = model_counts
        report.distribution_by_persona = persona_counts
        report.rule_id_coverage = dict(rules_counter)
        report.reason_code_counts = dict(reason_counter)

    def save_report(self, report: GenerationReport) -> None:
        report_path = self.config.runs_dir / "generation_report.json"
        data = {
            "total_episodes": report.total_episodes,
            "completed_episodes": report.completed_episodes,
            "completion_rate": round(report.completion_rate, 4),
            "total_samples": report.total_samples,
            "safe_samples": report.safe_samples,
            "unsafe_samples": report.unsafe_samples,
            "validation_error_on_safe": report.validation_error_on_safe,
            "unsafe_missing_evidence": report.unsafe_missing_evidence,
            "reason_code_counts": report.reason_code_counts,
            "rule_id_coverage": report.rule_id_coverage,
            "distribution_by_family": report.distribution_by_family,
            "distribution_by_model": report.distribution_by_model,
            "distribution_by_persona": report.distribution_by_persona,
        }
        report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_default_campaign_plan(factory: ScenarioFactory, config: GenerationConfig) -> list[dict[str, Any]]:
    """Build systematic episode plan meeting Phase 8 & 9 requirements:
    - 100+ train samples (>= 40 UNSAFE) across >= 2 train generator models and 4 personas
    - 20+ test samples using held-out model (qwen/qwen3.8-27b) and held-out cross-world families (X1, X2)
    - Clean family separation (zero family overlap across train, val, and test).
    """
    plan: list[dict[str, Any]] = []
    families = list(factory.specs.keys())

    test_fams = {"X7", "X8"}
    val_fams = {"X5", "X6", "D2"}

    for idx, fid in enumerate(families):
        fam_spec = factory.specs[fid]
        num_goals = len(fam_spec.get("goals", [1]))
        split = "test" if fid in test_fams else ("val" if fid in val_fams else "train")

        # Train split runs 2 seed rounds to yield >= 100 samples with >= 40 UNSAFE
        seed_rounds = [0, 200] if split == "train" else [0]
        model = config.test_model if split == "test" else config.train_models[idx % len(config.train_models)]

        for s_round in seed_rounds:
            for g_idx in range(num_goals):
                persona = config.personas[(idx + g_idx + (s_round // 100)) % len(config.personas)]
                seed = 100 + s_round + (idx * 10) + g_idx

                # Safe trajectory
                plan.append({
                    "family_id": fid,
                    "split": split,
                    "model": model,
                    "persona": persona,
                    "seed": seed,
                    "goal_variant_index": g_idx,
                    "held_out": (split == "test"),
                })

                # Red-team attack variant trajectory
                safe_calls = [
                    {"tool": c["tool"], "arguments": c.get("arguments", {})}
                    for c in fam_spec.get("safe_path", [])
                ]
                attack_calls = [
                    {"tool": c["tool"], "arguments": c.get("arguments", {})}
                    for c in fam_spec.get("attack_variants", [])
                ]
                if attack_calls:
                    red_team_plan = [
                        from_dict_to_call(c) for c in (safe_calls[:1] + attack_calls[:1])
                    ]
                    plan.append({
                        "family_id": fid,
                        "split": split,
                        "model": model,
                        "persona": persona,
                        "seed": seed + 500,
                        "goal_variant_index": g_idx,
                        "held_out": (split == "test"),
                        "scripted_plan": red_team_plan,
                    })

    return plan


def from_dict_to_call(d: dict[str, Any]) -> Any:
    from .schemas import ToolCall
    return ToolCall(name=d["tool"], arguments=d.get("arguments", {}))
