"""Scenario factory and mutator for ToolSafeSim (Phase 6).

Compiles structured scenario templates from YAML into validated Scenario instances.
Supports:
- Goal paraphrasing mutators.
- Cross-world multi-pack loading.
- Injection placement from injections.json.
- Deterministic seed generation.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional
import yaml

from ..fixtures.loader import load_pack, load_packs
from ..schemas import (
    AttackMeta,
    AuthorizationPolicy,
    InitialState,
    Scenario,
    ToolCall,
    UntrustedContent,
)
from ..state import WorldState

SPECS_DIR = Path(__file__).parent / "specs"


def load_all_specs(specs_dir: Path = SPECS_DIR) -> dict[str, dict[str, Any]]:
    """Load all scenario family specifications from YAML files."""
    specs: dict[str, dict[str, Any]] = {}
    for p in specs_dir.glob("*.yaml"):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data and "families" in data:
            for fam_id, fam_data in data["families"].items():
                specs[fam_id] = fam_data
    return specs


class ScenarioFactory:
    def __init__(self, specs_dir: Path = SPECS_DIR):
        self.specs = load_all_specs(specs_dir)

    def create_scenario(
        self,
        family_id: str,
        seed: int = 1,
        goal_variant_index: int = 0,
        held_out: bool = False,
    ) -> tuple[Scenario, WorldState, list[ToolCall], list[ToolCall]]:
        """Instantiate a Scenario and its initial WorldState, returning (scenario, state, safe_path, attack_variants)."""
        if family_id not in self.specs:
            raise KeyError(f"Unknown scenario family: {family_id}")

        fam = self.specs[family_id]
        goals = fam.get("goals", ["Execute task"])
        selected_goal = goals[goal_variant_index % len(goals)]

        # Determine pack(s)
        packs = fam.get("fixture_packs") or [fam.get("fixture_pack", "productivity_demo_v1")]
        
        # Load world state
        if len(packs) == 1:
            state = load_pack(packs[0], user_goal=selected_goal)
        else:
            scalar_owner = fam.get("scalar_owner")
            state = load_packs(packs, user_goal=selected_goal, scalar_owner=scalar_owner)

        # Untrusted injection
        untrusted: list[UntrustedContent] = []
        if "untrusted_injection" in fam:
            inj = fam["untrusted_injection"]
            untrusted.append(UntrustedContent(
                location=inj.get("location", ""),
                kind="injection",
                text=inj.get("text", "")
            ))

        scenario_id = f"sc_{family_id}_s{seed}_g{goal_variant_index}"
        if held_out:
            scenario_id += "_heldout"

        scenario = Scenario(
            scenario_id=scenario_id,
            scenario_family_id=family_id,
            category=fam.get("category", "productivity"),
            seed=seed,
            user_goal=selected_goal,
            initial_state=InitialState(
                fixture_pack=packs[0],
                overrides={"fixture_packs": packs} if len(packs) > 1 else {},
            ),
            authorization=AuthorizationPolicy(
                allowed_actions=fam.get("allowed_actions", []),
                forbidden_actions=fam.get("forbidden_actions", []),
                allowed_recipients=fam.get("allowed_recipients", []),
                approval_required=fam.get("approval_required", []),
                approvals_granted=fam.get("approvals_granted", []),
            ),
            untrusted_content=untrusted,
            attack=AttackMeta(
                type="prompt_injection" if untrusted else "none",
                location=untrusted[0].location if untrusted else None,
            ),
        )

        safe_path = [ToolCall(name=c["tool"], arguments=c.get("arguments", {})) for c in fam.get("safe_path", [])]
        attack_variants = [ToolCall(name=c["tool"], arguments=c.get("arguments", {})) for c in fam.get("attack_variants", [])]

        return scenario, state, safe_path, attack_variants
