"""Append-only JSONL event logger with stable, sequential event IDs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .schemas import EventType, TraceEvent


class EventLogger:
    def __init__(self, episode_id: str, scenario_id: str, path: Optional[Path] = None):
        self.episode_id = episode_id
        self.scenario_id = scenario_id
        self.path = Path(path) if path else None
        self.events: list[TraceEvent] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, type: EventType, step: int, payload: dict[str, Any],
               state_version: int = 0, state_hash: str = "") -> TraceEvent:
        seq = len(self.events)
        ev = TraceEvent(
            event_id=f"{self.episode_id}_e{seq:04d}", episode_id=self.episode_id,
            scenario_id=self.scenario_id, seq=seq, step=step, type=type,
            payload=payload, state_version=state_version, state_hash=state_hash,
        )
        self.events.append(ev)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:  # append-only
                f.write(ev.model_dump_json() + "\n")
        return ev

    @staticmethod
    def load(path: Path) -> list[TraceEvent]:
        with Path(path).open(encoding="utf-8") as f:
            return [TraceEvent.model_validate_json(line) for line in f if line.strip()]
