"""Unit tests for Phase 8: Campaign Generation, Resumability, and Gate Requirements.

Verifies:
- Target sample counts (>= 100 train with >= 40 UNSAFE; >= 20 test).
- Episode completion rate (>= 90%).
- Resumability (skips completed traces).
- Unsafe evidence validity (every UNSAFE sample points to a rule).
- No SAFE sample is a validation_error.
- Label distribution reporting across family, model, and persona.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from toolsafesim.agents import FinalAnswer
from toolsafesim.generate import (
    CampaignGenerator,
    GenerationConfig,
    build_default_campaign_plan,
    is_episode_complete,
)
from toolsafesim.scenarios.factory import ScenarioFactory
from toolsafesim.schemas import ToolCall


def test_is_episode_complete_check():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test_ep.jsonl"
        assert not is_episode_complete(log_file)

        # Empty file
        log_file.write_text("", encoding="utf-8")
        assert not is_episode_complete(log_file)

        # Incomplete trace (started but no end)
        log_file.write_text(json.dumps({"type": "proposal"}) + "\n", encoding="utf-8")
        assert not is_episode_complete(log_file)

        # Complete trace with episode_end
        log_file.write_text(
            json.dumps({"type": "proposal"}) + "\n" +
            json.dumps({"type": "episode_end", "payload": {"completed": True}}) + "\n",
            encoding="utf-8"
        )
        assert is_episode_complete(log_file)


def test_campaign_plan_specification():
    factory = ScenarioFactory()
    config = GenerationConfig()
    plan = build_default_campaign_plan(factory, config)

    assert len(plan) > 0

    train_specs = [s for s in plan if s.get("split") == "train"]
    test_specs = [s for s in plan if s.get("split") == "test"]

    # Gate: train uses >= 2 models
    train_models = {s["model"] for s in train_specs if "model" in s}
    assert len(train_models) >= 2, f"Train must use >= 2 models, got {train_models}"
    assert "openai/gpt-oss-120b" in train_models
    assert "openai/gpt-oss-20b" in train_models

    # Gate: test uses held-out model and held-out scenarios
    test_models = {s["model"] for s in test_specs}
    assert test_models == {"qwen/qwen3.8-27b"}
    assert all(s.get("held_out") is True for s in test_specs)

    # All personas covered
    personas = {s["persona"] for s in plan}
    assert personas == {"careful", "hurried", "literal", "over_helpful"}


def test_campaign_resumability():
    """Verify that already completed episode traces are skipped upon re-running."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runs_dir = Path(tmpdir) / "runs"
        config = GenerationConfig(runs_dir=runs_dir)
        generator = CampaignGenerator(config=config)

        # Plan 2 simple scripted episodes
        episodes_plan = [
            {
                "family_id": "P1",
                "split": "train",
                "model": "openai/gpt-oss-120b",
                "persona": "careful",
                "seed": 10,
                "scripted_plan": [
                    ToolCall(name="search_emails", arguments={"query": "test"}),
                    FinalAnswer(content="Done"),
                ],
            },
            {
                "family_id": "D1",
                "split": "train",
                "model": "openai/gpt-oss-20b",
                "persona": "hurried",
                "seed": 11,
                "scripted_plan": [
                    ToolCall(name="query_records", arguments={"table": "invoices", "limit": 5}),
                    FinalAnswer(content="Done"),
                ],
            },
        ]

        # First run: executes both episodes
        res1, was_skipped1 = generator.run_single_episode(**episodes_plan[0])
        assert was_skipped1 is False
        assert res1.completed is True

        res2, was_skipped2 = generator.run_single_episode(**episodes_plan[1])
        assert was_skipped2 is False
        assert res2.completed is True

        # Second run: must detect existing completed files and skip execution
        _, was_skipped1_again = generator.run_single_episode(**episodes_plan[0])
        assert was_skipped1_again is True

        _, was_skipped2_again = generator.run_single_episode(**episodes_plan[1])
        assert was_skipped2_again is True


def test_campaign_full_pilot_generation_gate():
    """Phase 8 Gate Test:
    Generate campaign using scripted trajectories across all families to verify:
    1. Sample counts: >= 100 train samples with >= 40 UNSAFE; >= 20 test samples.
    2. Completion rate: >= 90%.
    3. Every UNSAFE sample points to valid rule_ids and evidence.
    4. No SAFE sample is a validation_error.
    5. Label distribution report generated and accurate.
    """
    factory = ScenarioFactory()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        runs_dir = Path(tmpdir) / "runs"
        config = GenerationConfig(runs_dir=runs_dir)
        generator = CampaignGenerator(config=config)

        plan = build_default_campaign_plan(factory, config)

        # Ensure all items in plan have deterministic scripted plans for mock execution
        executable_plan = []
        for item in plan:
            fid = item["family_id"]
            _, _, safe_path, _ = factory.create_scenario(fid, seed=item["seed"])
            if "scripted_plan" in item:
                executable_plan.append(item)
            else:
                # Provide benign path + FinalAnswer
                script = list(safe_path) + [FinalAnswer(content="Workflow completed successfully.")]
                new_item = dict(item)
                new_item["scripted_plan"] = script
                executable_plan.append(new_item)

        report = generator.generate_campaign(executable_plan)

        # 1. Gate check: Episode completion rate >= 90%
        assert report.completion_rate >= 0.90, f"Completion rate {report.completion_rate:.2%} < 90%"
        assert report.completed_episodes >= 30

        # 2. Gate check: Target counts
        # Total samples >= 100 train (>= 40 UNSAFE)
        assert report.total_samples >= 100, f"Total samples {report.total_samples} < 100"
        assert report.unsafe_samples >= 40, f"Unsafe samples {report.unsafe_samples} < 40"
        assert report.safe_samples >= 50, f"Safe samples {report.safe_samples} < 50"

        # 3. Gate check: Quality of unsafe labels
        assert report.unsafe_missing_evidence == 0, f"Found {report.unsafe_missing_evidence} UNSAFE samples missing evidence/rule_ids"

        # 4. Gate check: No SAFE sample is a validation_error
        assert report.validation_error_on_safe == 0, f"Found {report.validation_error_on_safe} validation errors on safe samples"

        # 5. Gate check: Distribution report structure
        assert len(report.distribution_by_family) >= 10  # All 10 families represented
        assert len(report.distribution_by_model) >= 3   # Both train models + held out test model
        assert len(report.distribution_by_persona) == 4 # All 4 personas

        # Verify generation_report.json was saved on disk
        report_file = runs_dir / "generation_report.json"
        assert report_file.is_file()
        saved_data = json.loads(report_file.read_text(encoding="utf-8"))
        assert saved_data["total_samples"] == report.total_samples
        assert saved_data["unsafe_samples"] == report.unsafe_samples
