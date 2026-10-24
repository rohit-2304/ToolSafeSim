"""Tool contract, registry and execution wrapper.

Tools are MECHANICAL: they validate arguments, check that objects exist and enforce hard
sandbox constraints (virtual paths, immutable flags, ACL of the agent identity, allowlists).
They do NOT decide whether a call is *safe* – that is the reference labeler's job (Phase 4).
The controller must only call `run_tool` after a SAFE decision. No handler touches the network,
host filesystem, subprocesses or real credentials.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from ..schemas import SideEffect, ToolCall
from ..state import WorldState

AGENT = "service_agent"  # identity whose actions are evaluated


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Raised by handlers for structured, non-crashing failures."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = {}
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    kind: str = "ok"  # ok | tool_error | validation_error | unknown_tool  (tracked separately from safety)
    mutated: bool = False
    state_version: int = 0

    def observation_text(self) -> str:
        """Deterministic text shown to the agent (and stored as the tool observation)."""
        body = ({"ok": True, **self.data} if self.ok else
                {"ok": False, "error": {"code": self.error_code, "message": self.error_message}})
        return json.dumps(body, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    description: str
    side_effect: SideEffect
    args_model: type[ToolArgs]
    handler: Callable[[WorldState, Any], dict[str, Any]]
    required_permissions: tuple[str, ...] = ()

    @property
    def mutating(self) -> bool:
        return self.side_effect is not SideEffect.NONE

    def schema(self) -> dict[str, Any]:
        js = self.args_model.model_json_schema()
        js.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": js}


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, domain: str, description: str, side_effect: SideEffect, args: type[ToolArgs],
         perms: tuple[str, ...] = ()):
    def deco(fn):
        assert name not in REGISTRY, f"duplicate tool {name}"
        REGISTRY[name] = ToolSpec(name, domain, description, side_effect, args, fn, perms)
        return fn
    return deco


# --------------------------------------------------------------------------- shared helpers
def clock(state: WorldState) -> str:
    """Deterministic logical timestamp: simulated `now` + one minute per accepted mutation."""
    t = datetime.fromisoformat(state.now.replace("Z", "+00:00")) + timedelta(minutes=state.version)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def snippet(text: str, n: int = 140) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def tool_schemas() -> list[dict[str, Any]]:
    """Agent-visible tool descriptions (stable order)."""
    return [REGISTRY[k].schema() for k in sorted(REGISTRY)]


def run_tool(state: WorldState, call: ToolCall) -> ToolResult:
    """Validate and execute one call. Mutates `state` only on success of a mutating tool."""
    spec = REGISTRY.get(call.name)
    if spec is None:
        return ToolResult(ok=False, error_code="unknown_tool", kind="unknown_tool",
                          error_message=f"unknown tool '{call.name}'. Available: {', '.join(sorted(REGISTRY))}",
                          state_version=state.version)
    try:
        args = spec.args_model.model_validate(call.arguments)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors())
        return ToolResult(ok=False, error_code="invalid_arguments", kind="validation_error",
                          error_message=msg, state_version=state.version)
    before = state.version
    # work on a scratch copy so a handler that fails midway can never leave partial mutations
    scratch = state.snapshot()
    try:
        data = spec.handler(scratch, args)
    except ToolError as e:
        return ToolResult(ok=False, error_code=e.code, error_message=e.message, kind="tool_error",
                          state_version=before)
    for field in WorldState.model_fields:  # commit the scratch state atomically
        setattr(state, field, getattr(scratch, field))
    return ToolResult(ok=True, data=data, mutated=state.version != before, state_version=state.version)


def preview_tool(state: WorldState, call: ToolCall) -> ToolResult:
    """Dry-run on a deep copy: shows what would happen, never changes `state`."""
    return run_tool(state.snapshot(), call)
