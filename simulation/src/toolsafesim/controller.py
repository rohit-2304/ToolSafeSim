"""Simulation Controller for ToolSafeSim (Phase 5).

Orchestrates the interactive stepping loop between:
- Agent (proposes next action / tool call)
- Reference Policy Labeler (evaluates proposed call against security contract)
- Tool Execution Engine (executes ONLY safe calls; unsafe calls are blocked)
- Append-Only Event Logger (captures trace events, state versions, and hashes)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union
from .agents.base import Agent, AgentView, FinalAnswer, MalformedAction
from .logger import EventLogger
from .policy import label
from .schemas import ContextItem, Decision, Label, Scenario, ToolCall, TraceEvent
from .state import WorldState
from .tools import REGISTRY, run_tool, tool_schemas


@dataclass
class EpisodeResult:
    episode_id: str
    scenario_id: str
    steps_taken: int
    completed: bool
    terminated_by_policy: bool
    final_answer: Optional[str] = None
    final_decision: Optional[Decision] = None
    events: list[TraceEvent] = field(default_factory=list)
    state_hash_final: str = ""


class Controller:
    def __init__(
        self,
        scenario: Scenario,
        initial_state: WorldState,
        agent: Agent,
        episode_id: Optional[str] = None,
        log_path: Optional[Path] = None,
    ):
        self.scenario = scenario
        self.state = initial_state
        self.agent = agent
        self.episode_id = episode_id or f"ep_{scenario.scenario_id}_{scenario.seed}"
        self.logger = EventLogger(self.episode_id, scenario.scenario_id, log_path)
        
        # Interactive interaction context history
        self.history: list[ContextItem] = []
        self.step = 0
        self.available_tools_schema = tool_schemas()

    def run(self) -> EpisodeResult:
        """Run the episode stepping loop until completion or policy block."""
        # Record episode start
        start_payload: dict[str, Any] = {
            "scenario_id": self.scenario.scenario_id,
            "user_goal": self.scenario.user_goal,
            "policy_version": self.scenario.policy_version,
        }
        if hasattr(self.agent, "meta") and callable(getattr(self.agent, "meta")):
            start_payload.update(self.agent.meta())

        self.logger.record(
            type="episode_start",
            step=0,
            payload=start_payload,
            state_version=self.state.version,
            state_hash=self.state.hash(),
        )

        completed = False
        terminated_by_policy = False
        final_answer: Optional[str] = None
        last_decision: Optional[Decision] = None

        while self.step < self.scenario.max_steps:
            self.step += 1
            
            # 1. Provide view to agent and get proposed action
            view = AgentView(
                user_goal=self.scenario.user_goal,
                history=list(self.history),
                available_tools=self.available_tools_schema,
            )
            action = self.agent.next_action(view)

            # 2. Check if agent declared final answer
            if isinstance(action, FinalAnswer):
                final_answer = action.content
                self.logger.record(
                    type="final_answer",
                    step=self.step,
                    payload={"content": final_answer},
                    state_version=self.state.version,
                    state_hash=self.state.hash(),
                )
                completed = True
                break

            # 3. Check if action was malformed JSON/output
            if isinstance(action, MalformedAction):
                self.logger.record(
                    type="malformed",
                    step=self.step,
                    payload={"raw": action.raw_content, "error": action.error_message},
                    state_version=self.state.version,
                    state_hash=self.state.hash(),
                )
                obs_text = json.dumps({"ok": False, "error": {"code": "validation_error", "message": action.error_message}})
                self.history.append(ContextItem(role="tool", observation=obs_text))
                continue

            # 4. Agent proposed a ToolCall
            assert isinstance(action, ToolCall), f"Invalid agent action type: {type(action)}"
            tool_call = action

            self.logger.record(
                type="proposal",
                step=self.step,
                payload={"name": tool_call.name, "arguments": tool_call.arguments},
                state_version=self.state.version,
                state_hash=self.state.hash(),
            )

            # 4. Evaluate proposal using reference policy labeler BEFORE execution
            decision = label(self.scenario, self.state, self.history, tool_call)
            last_decision = decision

            self.logger.record(
                type="decision",
                step=self.step,
                payload=decision.model_dump(mode="json"),
                state_version=self.state.version,
                state_hash=self.state.hash(),
            )

            # 5. Branch based on safety decision
            if decision.label == Label.UNSAFE:
                # Security boundary: Block execution, record event, terminate episode immediately
                self.logger.record(
                    type="blocked",
                    step=self.step,
                    payload={
                        "reason_code": decision.reason_code.value,
                        "evidence": decision.evidence,
                        "rule_ids": decision.rule_ids,
                    },
                    state_version=self.state.version,
                    state_hash=self.state.hash(),
                )
                terminated_by_policy = True
                break

            # 6. SAFE decision -> Execute the tool in simulator
            pre_hash = self.state.hash()
            result = run_tool(self.state, tool_call)

            # Add proposal and observation to history
            self.history.append(
                ContextItem(
                    role="assistant_tool_call",
                    tool=tool_call.name,
                    arguments=tool_call.arguments,
                )
            )
            obs_text = result.observation_text()
            self.history.append(
                ContextItem(
                    role="tool",
                    observation=obs_text,
                )
            )

            self.logger.record(
                type="tool_result",
                step=self.step,
                payload={
                    "ok": result.ok,
                    "kind": result.kind,
                    "error_code": result.error_code,
                    "data": result.data,
                    "observation": obs_text,
                },
                state_version=self.state.version,
                state_hash=self.state.hash(),
            )

            if result.mutated:
                self.logger.record(
                    type="state_transition",
                    step=self.step,
                    payload={"tool": tool_call.name, "pre_hash": pre_hash, "post_hash": self.state.hash()},
                    state_version=self.state.version,
                    state_hash=self.state.hash(),
                )

        # Record episode end
        self.logger.record(
            type="episode_end",
            step=self.step,
            payload={
                "completed": completed,
                "terminated_by_policy": terminated_by_policy,
                "total_steps": self.step,
            },
            state_version=self.state.version,
            state_hash=self.state.hash(),
        )

        return EpisodeResult(
            episode_id=self.episode_id,
            scenario_id=self.scenario.scenario_id,
            steps_taken=self.step,
            completed=completed,
            terminated_by_policy=terminated_by_policy,
            final_answer=final_answer,
            final_decision=last_decision,
            events=self.logger.events,
            state_hash_final=self.state.hash(),
        )
