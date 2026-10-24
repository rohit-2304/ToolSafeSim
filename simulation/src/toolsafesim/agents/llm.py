"""LLM Agent Adapter for ToolSafeSim (Phase 7).

Integrates Groq client with OpenAI-style tool-calling, persona prompts,
and structured tool-call and final-answer parsing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from ..schemas import ToolCall
from .base import Agent, AgentView, FinalAnswer, MalformedAction
from .groq_client import GroqClient

CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "generators.yaml"


def load_generators_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    if config_path.is_file():
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {}


class LLMAgent(Agent):
    """An agent that uses an LLM to propose tool calls or output final answers."""

    def __init__(
        self,
        client: GroqClient | None = None,
        model: str = "openai/gpt-oss-120b",
        persona: str = "careful",
        temperature: float = 0.0,
        seed: int = 42,
        system_prompt_override: str | None = None,
        config_path: Path = CONFIG_PATH,
    ):
        self.client = client or GroqClient()
        self.model = model
        self.persona = persona
        self.temperature = temperature
        self.seed = seed
        self.config = load_generators_config(config_path)

        # Build system prompt
        if system_prompt_override:
            self.system_prompt = system_prompt_override
        else:
            persona_data = self.config.get("personas", {}).get(persona, {})
            base_prompt = persona_data.get("system_prompt", "You are a helpful and diligent assistant.")
            self.system_prompt = base_prompt.strip()

        self.system_prompt_hash = hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()

    def meta(self) -> dict[str, Any]:
        """Metadata for audit trail and event logging."""
        return {
            "generator_model": self.model,
            "generator_version": "0.1",
            "persona": self.persona,
            "system_prompt_hash": self.system_prompt_hash,
            "temperature": self.temperature,
            "seed": self.seed,
        }

    def format_messages(self, view: AgentView) -> list[dict[str, Any]]:
        """Format view history into OpenAI chat messages format."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"User Goal: {view.user_goal}"},
        ]

        # Convert history items into valid multi-turn tool call sequence
        call_id_counter = 0
        current_tool_id: str | None = None

        for item in view.history:
            if item.role == "assistant_tool_call":
                call_id_counter += 1
                current_tool_id = f"call_{call_id_counter}"
                args_str = json.dumps(item.arguments or {}, ensure_ascii=False)
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": current_tool_id,
                            "type": "function",
                            "function": {
                                "name": item.tool or "unknown_tool",
                                "arguments": args_str,
                            },
                        }
                    ],
                })
            elif item.role == "tool":
                tid = current_tool_id or f"call_{call_id_counter or 1}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": item.observation or "",
                })

        return messages

    @staticmethod
    def format_tools(available_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format ToolSpec schemas into OpenAI tool definitions."""
        tools: list[dict[str, Any]] = []
        for t in available_tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return tools

    def next_action(self, view: AgentView) -> ToolCall | FinalAnswer | MalformedAction:
        """Propose next action by querying the LLM via GroqClient."""
        messages = self.format_messages(view)
        tools = self.format_tools(view.available_tools)

        try:
            response = self.client.chat_completion(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
                temperature=self.temperature,
                seed=self.seed,
            )
        except RuntimeError as e:
            err_str = str(e)
            if "tool_use_failed" in err_str or "Tool call validation failed" in err_str or "HTTP Error 400" in err_str:
                try:
                    json_start = err_str.find("{")
                    if json_start != -1:
                        err_json = json.loads(err_str[json_start:])
                        failed_gen = err_json.get("error", {}).get("failed_generation")
                        if failed_gen:
                            gen_data = json.loads(failed_gen)
                            if isinstance(gen_data, dict) and "name" in gen_data:
                                return ToolCall(name=gen_data["name"], arguments=gen_data.get("arguments", {}))
                except Exception:
                    pass
                return MalformedAction(
                    raw_content=err_str,
                    error_message=f"LLM tool use rejected by API: {err_str}",
                )
            raise

        choices = response.get("choices", [])
        if not choices:
            return FinalAnswer(content="No response choices returned by model.")

        choice = choices[0]
        message = choice.get("message", {})

        # 1. Check for tool calls
        tool_calls = message.get("tool_calls")
        if tool_calls and len(tool_calls) > 0:
            first_call = tool_calls[0]
            fn = first_call.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")

            if isinstance(raw_args, dict):
                args_dict = raw_args
            else:
                try:
                    args_dict = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    return MalformedAction(
                        raw_content=str(raw_args),
                        error_message=f"Failed to parse tool call JSON arguments: {e}",
                    )

            if not isinstance(args_dict, dict):
                return MalformedAction(
                    raw_content=str(raw_args),
                    error_message=f"Tool call arguments must be a JSON object, got {type(args_dict).__name__}",
                )

            return ToolCall(name=name, arguments=args_dict)

        # 2. Check for assistant final answer content
        content = message.get("content")
        if content:
            return FinalAnswer(content=content.strip())

        return FinalAnswer(content="Task completed.")
