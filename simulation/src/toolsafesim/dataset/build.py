"""Trace parser and sample extractor for ToolSafeSim (Phase 9).

Extracts canonical Sample instances from raw chronological episode JSONL logs:
- Builds model-ready context history per decision point.
- Enforces strict projection: Sample.model_input() hides sensitive/evaluation metadata.
- Deduplicates identical samples.
- Filters out UNRESOLVED_REVIEW into diagnostics.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import (
    ContextItem,
    Label,
    ReasonCode,
    Sample,
    ToolCall,
)


@dataclass
class DatasetBundle:
    samples: list[Sample] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def compute_sample_input_hash(user_goal: str, context: list[ContextItem], call: ToolCall) -> str:
    """Compute deterministic SHA-256 hash over classifier-visible model input."""
    obj = {
        "user_goal": user_goal,
        "context": [c.model_dump(mode="json", exclude_none=True) for c in context],
        "proposed_tool_call": call.model_dump(mode="json"),
    }
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_dataset_from_traces(traces_dir: Path | str) -> DatasetBundle:
    """Scan directory of episode traces and extract deduplicated Samples and diagnostics."""
    traces_path = Path(traces_dir)
    bundle = DatasetBundle()

    seen_hashes: dict[str, tuple[str, Label]] = {}  # input_hash -> (sample_id, label)
    scenario_ids_seen: set[str] = set()

    trace_files = sorted(traces_path.glob("*.jsonl"))

    for p in trace_files:
        lines = [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            continue

        raw_events = [json.loads(line) for line in lines]
        bundle.traces.append({"trace_file": p.name, "event_count": len(raw_events), "events": raw_events})

        # Parse episode start metadata
        start_event = raw_events[0]
        payload = start_event.get("payload", {})
        scenario_id = payload.get("scenario_id", p.stem)
        user_goal = payload.get("user_goal", "")

        # Extract family ID from scenario_id
        # Format usually: sc_{family}_s{seed}_g{variant}
        parts = scenario_id.split("_")
        family_id = parts[1] if len(parts) > 1 else "unknown"

        if scenario_id not in scenario_ids_seen:
            scenario_ids_seen.add(scenario_id)
            bundle.scenarios.append({
                "scenario_id": scenario_id,
                "family_id": family_id,
                "user_goal": user_goal,
                "start_payload": payload,
            })

        # Chronological playback to construct context history
        history: list[ContextItem] = []
        pending_proposals: dict[int, ToolCall] = {}
        ep_seen_calls: set[str] = set()

        for event in raw_events:
            etype = event.get("type")
            step = event.get("step", 0)
            epayload = event.get("payload", {})

            if etype == "proposal":
                name = epayload.get("name", "")
                arguments = epayload.get("arguments", {})
                pending_proposals[step] = ToolCall(name=name, arguments=arguments)

            elif etype == "decision":
                label_str = epayload.get("label", "SAFE")
                reason_str = epayload.get("reason_code", "PERMITTED_ACTION")
                evidence = epayload.get("evidence", [])

                label = Label(label_str)
                reason_code = ReasonCode(reason_str)

                # Gate rule: UNRESOLVED_REVIEW cases excluded from dataset
                if reason_code == ReasonCode.UNRESOLVED_REVIEW:
                    bundle.diagnostics.append({
                        "issue": "unresolved_review_excluded",
                        "trace_file": p.name,
                        "step": step,
                        "scenario_id": scenario_id,
                        "payload": epayload,
                    })
                    continue

                tool_call = pending_proposals.get(step)
                if not tool_call:
                    continue

                # Intra-episode retry loop suppression:
                # If an agent calls the identical tool with identical arguments repeatedly in the same episode,
                # keep the first occurrence and skip subsequent redundant loop steps.
                call_sig = json.dumps({"tool": tool_call.name, "arguments": tool_call.arguments}, sort_keys=True)
                if call_sig in ep_seen_calls:
                    bundle.diagnostics.append({
                        "issue": "intra_episode_loop_deduped",
                        "trace_file": p.name,
                        "step": step,
                        "tool": tool_call.name,
                    })
                    continue
                ep_seen_calls.add(call_sig)

                input_hash = compute_sample_input_hash(user_goal, history, tool_call)

                # Deduplication check
                if input_hash in seen_hashes:
                    existing_id, existing_label = seen_hashes[input_hash]
                    if existing_label != label:
                        # Conflicting labels on identical input is a dataset integrity violation
                        bundle.diagnostics.append({
                            "issue": "conflicting_label_duplicate",
                            "input_hash": input_hash,
                            "existing_sample_id": existing_id,
                            "existing_label": existing_label.value,
                            "new_label": label.value,
                            "trace_file": p.name,
                            "step": step,
                        })
                    else:
                        bundle.diagnostics.append({
                            "issue": "duplicate_sample_deduped",
                            "input_hash": input_hash,
                            "kept_sample_id": existing_id,
                            "dropped_file": p.name,
                            "step": step,
                        })
                    continue

                sample_id = f"smp_{p.stem}_st{step}"
                sample = Sample(
                    sample_id=sample_id,
                    scenario_id=scenario_id,
                    scenario_family_id=family_id,
                    episode_id=p.stem,
                    step=step,
                    user_goal=user_goal,
                    context=list(history),
                    proposed_tool_call=tool_call,
                    label=label,
                    reason_code=reason_code,
                    evidence=evidence,
                    scenario_tags=[family_id],
                )

                seen_hashes[input_hash] = (sample_id, label)
                bundle.samples.append(sample)

            elif etype == "tool_result":
                # Append accepted step to history
                tool_call = pending_proposals.get(step)
                if tool_call:
                    history.append(
                        ContextItem(
                            role="assistant_tool_call",
                            tool=tool_call.name,
                            arguments=tool_call.arguments,
                        )
                    )
                obs = epayload.get("observation", "")
                history.append(ContextItem(role="tool", observation=obs))

    return bundle
