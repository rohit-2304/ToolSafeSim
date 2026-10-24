"""Fixture loader: frozen pack JSON -> WorldState, with deep-merge overrides."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

from ..state import WorldState

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures"


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if v is None:  # explicit None deletes a key (e.g. remove an email)
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_pack_dict(name: str, fixture_dir: Path = FIXTURE_DIR) -> dict[str, Any]:
    return json.loads((fixture_dir / f"{name}.json").read_text(encoding="utf-8"))


def load_pack(name: str, overrides: Optional[dict] = None, user_goal: str = "",
              fixture_dir: Path = FIXTURE_DIR) -> WorldState:
    data = load_pack_dict(name, fixture_dir)
    if overrides:
        data = deep_merge(data, overrides)
    data["user_goal"] = user_goal
    return WorldState.model_validate(data)


class PackMergeError(ValueError):
    pass


def load_packs(names: list[str], overrides: Optional[dict] = None, user_goal: str = "",
               scalar_owner: Optional[dict[str, str]] = None,
               fixture_dir: Path = FIXTURE_DIR) -> WorldState:
    """Merge several packs into one cross-world WorldState.

    dict stores: union (same key in two packs is an error unless the values are equal;
    principals with identical identity have their allowed_actions unioned);
    list stores: concatenated; counters: max per prefix; scalars (cwd, user_email, ...):
    taken from ``scalar_owner[field]`` if given, else the single non-empty value -- two
    different non-empty values without an owner is an error. Deterministic for a given
    ordered ``names``. A single name behaves exactly like ``load_pack``.
    """
    if len(set(names)) != len(names) or not names:
        raise PackMergeError("names must be non-empty and unique")
    scalar_owner = scalar_owner or {}
    packs = {n: load_pack_dict(n, fixture_dir) for n in names}
    for f, owner in scalar_owner.items():
        if owner not in packs:
            raise PackMergeError(f"scalar_owner[{f}]={owner} is not in names")
    merged: dict[str, Any] = {}
    for n in names:
        for field, val in packs[n].items():
            if field not in merged:
                merged[field] = copy.deepcopy(val)
            elif field == "counters":
                for k, c in val.items():
                    merged[field][k] = max(merged[field].get(k, 0), c)
            elif isinstance(val, dict):
                for k, v in val.items():
                    if field == "principals" and k in merged[field]:
                        cur = merged[field][k]
                        common = (set(cur) & set(v)) - {"allowed_actions"}
                        if all(cur[x] == v[x] for x in common):  # same identity (omitted keys are defaults)
                            acts = sorted(set(cur.get("allowed_actions", [])) | set(v.get("allowed_actions", [])))
                            cur.update(v)
                            if acts:
                                cur["allowed_actions"] = acts
                            continue
                    if k in merged[field] and merged[field][k] != v:
                        raise PackMergeError(f"collision in '{field}' key {k!r} (pack {n})")
                    merged[field][k] = copy.deepcopy(v)
            elif isinstance(val, list):
                merged[field] = merged[field] + copy.deepcopy(val)
            elif field in scalar_owner:
                continue
            elif val in (None, "", 0) or val == merged[field]:
                continue
            elif merged[field] in (None, "", 0):
                merged[field] = val
            else:
                raise PackMergeError(f"scalar '{field}' differs across packs; set scalar_owner")
    for f, owner in scalar_owner.items():
        if f in packs[owner]:
            merged[f] = packs[owner][f]
    merged["version"] = 0
    if overrides:
        merged = deep_merge(merged, overrides)
    merged["user_goal"] = user_goal
    return WorldState.model_validate(merged)
