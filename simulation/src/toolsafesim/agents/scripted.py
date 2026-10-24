"""Scripted deterministic agents for verification and test replay."""
from __future__ import annotations

from ..schemas import ToolCall
from .base import Agent, AgentView, FinalAnswer


class ScriptedAgent(Agent):
    """An agent that follows a predetermined list of actions sequentially."""

    def __init__(self, plan: list[ToolCall | FinalAnswer]):
        self.plan = list(plan)
        self.cursor = 0

    def next_action(self, view: AgentView) -> ToolCall | FinalAnswer:
        if self.cursor >= len(self.plan):
            return FinalAnswer(content="Plan completed.")
        action = self.plan[self.cursor]
        self.cursor += 1
        return action
