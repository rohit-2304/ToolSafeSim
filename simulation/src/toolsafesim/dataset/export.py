"""Dataset bundle export and manifest generation (Phase 9).

Exports to /home/rohit/Rohit/agent-security/data/:
- scenarios.jsonl
- traces.jsonl
- toolcall_dataset.jsonl
- splits/train.jsonl
- splits/val.jsonl
- splits/test.jsonl
- diagnostics.jsonl
- dataset_manifest.json (hashes, versions, counts, baseline results)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schemas import GENERATOR_VERSION, POLICY_VERSION, Sample
from .baselines import evaluate_baselines
from .build import DatasetBundle
from .split import DatasetSplits

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def write_jsonl(path: Path, items: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            if isinstance(item, Sample):
                line = json.dumps(item.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            else:
                line = json.dumps(item, sort_keys=True, ensure_ascii=False)
            f.write(line + "\n")


def export_dataset_bundle(
    bundle: DatasetBundle,
    splits: DatasetSplits,
    output_dir: Path | str = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Export complete dataset package and write dataset_manifest.json."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    splits_dir = out_path / "splits"
    splits_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write individual JSONL files
    scenarios_file = out_path / "scenarios.jsonl"
    traces_file = out_path / "traces.jsonl"
    all_dataset_file = out_path / "toolcall_dataset.jsonl"
    train_file = splits_dir / "train.jsonl"
    val_file = splits_dir / "val.jsonl"
    test_file = splits_dir / "test.jsonl"
    diagnostics_file = out_path / "diagnostics.jsonl"

    write_jsonl(scenarios_file, bundle.scenarios)
    write_jsonl(traces_file, bundle.traces)
    write_jsonl(all_dataset_file, bundle.samples)
    write_jsonl(train_file, splits.train)
    write_jsonl(val_file, splits.val)
    write_jsonl(test_file, splits.test)
    write_jsonl(diagnostics_file, bundle.diagnostics)

    # 2. Evaluate naive baselines
    baseline_metrics = evaluate_baselines(splits)

    # 3. Compute SHA-256 checksums
    checksums = {
        "scenarios.jsonl": file_sha256(scenarios_file),
        "traces.jsonl": file_sha256(traces_file),
        "toolcall_dataset.jsonl": file_sha256(all_dataset_file),
        "splits/train.jsonl": file_sha256(train_file),
        "splits/val.jsonl": file_sha256(val_file),
        "splits/test.jsonl": file_sha256(test_file),
        "diagnostics.jsonl": file_sha256(diagnostics_file),
    }

    # 4. Generate manifest
    safe_count = sum(1 for s in bundle.samples if s.label.value == "SAFE")
    unsafe_count = sum(1 for s in bundle.samples if s.label.value == "UNSAFE")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "generator_version": GENERATOR_VERSION,
        "total_samples": len(bundle.samples),
        "total_scenarios": len(bundle.scenarios),
        "total_traces": len(bundle.traces),
        "total_diagnostics": len(bundle.diagnostics),
        "label_distribution": {
            "SAFE": safe_count,
            "UNSAFE": unsafe_count,
        },
        "splits": {
            "train": len(splits.train),
            "val": len(splits.val),
            "test": len(splits.test),
        },
        "baseline_metrics": baseline_metrics,
        "sha256_checksums": checksums,
    }

    manifest_file = out_path / "dataset_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    return manifest
