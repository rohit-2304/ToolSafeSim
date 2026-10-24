"""Agent base interface and execution action models for ToolSafeSim."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..schemas import ContextItem, ToolCall


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinalAnswer(AgentAction):
    content: str


class MalformedAction(AgentAction):
    raw_content: str
    error_message: str


class AgentView(BaseModel):
    """The observation view provided to the agent at step t.
    
    Hides internal simulator metadata (e.g. trust_origin, attack_meta, hidden policy).
    """
    model_config = ConfigDict(extra="forbid")
    user_goal: str
    history: list[ContextItem]
    available_tools: list[dict[str, Any]]


class Agent(ABC):
    @abstractmethod
    def next_action(self, view: AgentView) -> ToolCall | FinalAnswer | MalformedAction:
        """Propose the next tool call, final answer, or malformed action."""
