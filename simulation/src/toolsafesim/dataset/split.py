"""Family-based dataset splitter with zero data leakage guarantees (Phase 9).

Splits by scenario family and held-out flags to ensure:
- No family overlap between train and test for held-out families.
- Zero scenario overlap across train, val, and test splits.
- Zero text duplicate overlap across splits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..schemas import Sample

DEFAULT_HELD_OUT_FAMILIES = ["X7", "X8"]  # Held-out cross-world families in test
DEFAULT_VAL_FAMILIES = ["X5", "X6", "D2"]


@dataclass
class DatasetSplits:
    train: list[Sample] = field(default_factory=list)
    val: list[Sample] = field(default_factory=list)
    test: list[Sample] = field(default_factory=list)
    split_meta: dict[str, Any] = field(default_factory=dict)


def compute_model_input_hash(sample: Sample) -> str:
    serialized = json.dumps(sample.model_input(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def split_dataset_by_family(
    samples: list[Sample],
    held_out_families: list[str] | None = None,
    val_families: list[str] | None = None,
) -> DatasetSplits:
    """Split samples into train, val, and test splits based strictly on family boundaries."""
    held_out_fams = set(held_out_families or DEFAULT_HELD_OUT_FAMILIES)
    val_fams = set(val_families or DEFAULT_VAL_FAMILIES)

    splits = DatasetSplits()

    for s in samples:
        # Rule 1: Held-out families strictly in test
        if s.scenario_family_id in held_out_fams:
            splits.test.append(s)
        # Rule 2: Validation families
        elif s.scenario_family_id in val_fams:
            splits.val.append(s)
        # Rule 3: Training set
        else:
            splits.train.append(s)

    # Compile split metadata
    splits.split_meta = {
        "train_count": len(splits.train),
        "val_count": len(splits.val),
        "test_count": len(splits.test),
        "train_families": sorted({s.scenario_family_id for s in splits.train}),
        "val_families": sorted({s.scenario_family_id for s in splits.val}),
        "test_families": sorted({s.scenario_family_id for s in splits.test}),
    }

    validate_splits_leakage(splits)
    return splits


def validate_splits_leakage(splits: DatasetSplits) -> None:
    """Rigorous leakage validation between splits."""
    train_scenarios = {s.scenario_id for s in splits.train}
    val_scenarios = {s.scenario_id for s in splits.val}
    test_scenarios = {s.scenario_id for s in splits.test}

    # 1. Zero scenario ID overlap
    assert not (train_scenarios & val_scenarios), f"Leakage: scenario overlap between train and val: {train_scenarios & val_scenarios}"
    assert not (train_scenarios & test_scenarios), f"Leakage: scenario overlap between train and test: {train_scenarios & test_scenarios}"
    assert not (val_scenarios & test_scenarios), f"Leakage: scenario overlap between val and test: {val_scenarios & test_scenarios}"

    # 2. Zero text duplicate overlap across splits
    train_hashes = {compute_model_input_hash(s) for s in splits.train}
    val_hashes = {compute_model_input_hash(s) for s in splits.val}
    test_hashes = {compute_model_input_hash(s) for s in splits.test}

    assert not (train_hashes & val_hashes), "Leakage: text duplicate between train and val"
    assert not (train_hashes & test_hashes), "Leakage: text duplicate between train and test"
    assert not (val_hashes & test_hashes), "Leakage: text duplicate between val and test"

    # 3. Strictly sanctioned model_input keys
    sanctioned_keys = {"user_goal", "context", "proposed_tool_call"}
    for split_name, split_samples in [("train", splits.train), ("val", splits.val), ("test", splits.test)]:
        for s in split_samples:
            keys = set(s.model_input().keys())
            assert keys == sanctioned_keys, f"Forbidden keys in {split_name} model_input(): {keys ^ sanctioned_keys}"
