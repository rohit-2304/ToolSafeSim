"""Reference policy labeler interface for ToolSafeSim (Phase 4)."""
from __future__ import annotations

from typing import Any
from ..schemas import Decision, Scenario, ToolCall
from ..state import WorldState
from .rules import evaluate_policy


def label(
    scenario: Scenario,
    state_before: WorldState,
    history: list[Any],
    call: ToolCall,
) -> Decision:
    """Label a proposed tool call as SAFE or UNSAFE with a detailed reason code,
    rule IDs, and evidence string.

    Guarantees:
    - Pure function of (scenario, state_before, history, call).
    - Completely independent of post-call execution or execution outcome.
    - Operates without seeing hidden simulation labels or agent prompts.
    """
    return evaluate_policy(scenario, state_before, history, call)
