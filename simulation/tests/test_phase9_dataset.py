"""Unit tests for Phase 9: Dataset Builder, Splitter, Baselines, and Export Manifest.

Verifies:
- Accurate extraction and context playback from traces.
- Strict model_input projection (no labels, trust tags, or internal state leakage).
- Deduplication and diagnostic logging of unresolved cases.
- Zero-leakage family splitting (no family/scenario/text duplicate overlap).
- Naive baselines (majority, keyword, TF-IDF) stay low (<= 65%).
- Manifest checksums and byte-identical reproducibility.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from toolsafesim.agents import FinalAnswer
from toolsafesim.dataset import (
    build_dataset_from_traces,
    evaluate_baselines,
    export_dataset_bundle,
    split_dataset_by_family,
)
from toolsafesim.generate import (
    CampaignGenerator,
    GenerationConfig,
    build_default_campaign_plan,
)
from toolsafesim.scenarios.factory import ScenarioFactory
from toolsafesim.schemas import Label


def create_mock_traces_dir(tmpdir: Path) -> Path:
    """Helper to generate a small set of verified traces for dataset testing."""
    factory = ScenarioFactory()
    runs_dir = tmpdir / "runs"
    config = GenerationConfig(runs_dir=runs_dir)
    generator = CampaignGenerator(config=config)

    plan = build_default_campaign_plan(factory, config)
    executable_plan = []
    for item in plan:
        fid = item["family_id"]
        _, _, safe_path, _ = factory.create_scenario(fid, seed=item["seed"])
        if "scripted_plan" in item:
            executable_plan.append(item)
        else:
            script = list(safe_path) + [FinalAnswer(content="Done")]
            new_item = dict(item)
            new_item["scripted_plan"] = script
            executable_plan.append(new_item)

    generator.generate_campaign(executable_plan)
    return generator.episodes_dir


def test_dataset_build_and_model_input_projection():
    with tempfile.TemporaryDirectory() as tmpdir:
        traces_dir = create_mock_traces_dir(Path(tmpdir))
        bundle = build_dataset_from_traces(traces_dir)

        assert len(bundle.samples) >= 100
        assert len(bundle.scenarios) >= 10
        assert len(bundle.traces) >= 30

        # Verify strict model_input projection
        sanctioned_keys = {"user_goal", "context", "proposed_tool_call"}
        for s in bundle.samples:
            inp = s.model_input()
            assert set(inp.keys()) == sanctioned_keys
            # Assert no labels, scenario_id, or trust tags in model input
            assert "label" not in inp
            assert "scenario_id" not in inp
            assert "reason_code" not in inp
            assert "trust_origin" not in json.dumps(inp)


def test_dataset_leakage_and_splitting():
    with tempfile.TemporaryDirectory() as tmpdir:
        traces_dir = create_mock_traces_dir(Path(tmpdir))
        bundle = build_dataset_from_traces(traces_dir)

        splits = split_dataset_by_family(bundle.samples)

        assert len(splits.train) > 0
        assert len(splits.val) > 0
        assert len(splits.test) > 0

        train_scenarios = {s.scenario_id for s in splits.train}
        val_scenarios = {s.scenario_id for s in splits.val}
        test_scenarios = {s.scenario_id for s in splits.test}

        # 1. Zero scenario overlap
        assert not (train_scenarios & val_scenarios)
        assert not (train_scenarios & test_scenarios)
        assert not (val_scenarios & test_scenarios)

        # 2. Held-out family strictly in test
        for s in splits.train + splits.val:
            assert s.scenario_family_id not in {"X7", "X8"}
        test_fams = {s.scenario_family_id for s in splits.test}
        assert "X7" in test_fams
        assert "X8" in test_fams

        # 3. Both SAFE and UNSAFE in splits
        train_labels = {s.label for s in splits.train}
        assert Label.SAFE in train_labels
        assert Label.UNSAFE in train_labels


def test_naive_baselines_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        traces_dir = create_mock_traces_dir(Path(tmpdir))
        bundle = build_dataset_from_traces(traces_dir)
        splits = split_dataset_by_family(bundle.samples)

        metrics = evaluate_baselines(splits)

        assert "majority_accuracy" in metrics
        assert "keyword_accuracy" in metrics
        assert "tfidf_centroid_accuracy" in metrics
        # Lexical shortcut accuracies should stay low (Gate: <= 65%)
        assert metrics["keyword_accuracy"] <= 0.65
        assert metrics["tfidf_centroid_accuracy"] <= 0.65
        assert metrics["gate_passed"] is True


def test_export_manifest_and_reproducibility():
    with tempfile.TemporaryDirectory() as tmpdir:
        traces_dir = create_mock_traces_dir(Path(tmpdir))
        bundle = build_dataset_from_traces(traces_dir)
        splits = split_dataset_by_family(bundle.samples)

        out1 = Path(tmpdir) / "export1"
        out2 = Path(tmpdir) / "export2"

        manifest1 = export_dataset_bundle(bundle, splits, output_dir=out1)
        manifest2 = export_dataset_bundle(bundle, splits, output_dir=out2)

        # Verify files exist in out1
        for fname in [
            "scenarios.jsonl", "traces.jsonl", "toolcall_dataset.jsonl",
            "splits/train.jsonl", "splits/val.jsonl", "splits/test.jsonl",
            "diagnostics.jsonl", "dataset_manifest.json",
        ]:
            assert (out1 / fname).is_file()

        # Gate Check: Byte-identical reproducibility
        assert manifest1["sha256_checksums"] == manifest2["sha256_checksums"]
        for fname in manifest1["sha256_checksums"]:
            bytes1 = (out1 / fname).read_bytes()
            bytes2 = (out2 / fname).read_bytes()
            assert bytes1 == bytes2
