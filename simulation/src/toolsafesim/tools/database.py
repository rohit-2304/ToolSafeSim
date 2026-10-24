"""Database tools: query_records, get_record, update_record, delete_record.

Restricted columns (state.restricted_fields) are excluded from default projections and only
returned when explicitly requested; whether requesting them is *safe* is the labeler's decision.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from ..schemas import SideEffect
from ..state import WorldState
from .base import ToolArgs, ToolError, tool

MAX_LIMIT = 100
MUTABLE = {
    "customers": {"name", "email", "region", "account_tier"},
    "invoices": {"status", "due_date", "amount", "purchase_order"},
    "orders": {"status", "shipping_region"},
    "support_cases": {"status", "assigned_team", "subject", "description"},
}
ENUMS = {
    ("invoices", "status"): {"paid", "open", "overdue"},
    ("orders", "status"): {"delivered", "processing", "cancelled"},
    ("support_cases", "status"): {"open", "pending", "resolved"},
    ("support_cases", "assigned_team"): {"tier1", "tier2", "billing"},
    ("customers", "account_tier"): {"standard", "premium", "enterprise"},
    ("customers", "region"): {"west", "east", "north", "south"},
}
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in"}


def _table(state: WorldState, name: str) -> dict[str, dict]:
    if name not in state.database:
        raise ToolError("unknown_table", f"unknown table '{name}'. Tables: {', '.join(sorted(state.database))}")
    return state.database[name]


def _columns(rows: dict[str, dict]) -> list[str]:
    return list(next(iter(rows.values())).keys()) if rows else []


def _match(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    for f, cond in filters.items():
        v = row.get(f)
        conds = cond if isinstance(cond, dict) else {"eq": cond}
        for op, ref in conds.items():
            try:
                ok = {"eq": lambda: v == ref, "ne": lambda: v != ref, "gt": lambda: v > ref, "gte": lambda: v >= ref,
                      "lt": lambda: v < ref, "lte": lambda: v <= ref, "in": lambda: v in ref}[op]()
            except TypeError:
                raise ToolError("invalid_filter", f"cannot compare field '{f}' with {ref!r}")
            if not ok:
                return False
    return True


def _validate_filters(cols: list[str], filters: dict[str, Any]):
    for f, cond in filters.items():
        if f not in cols:
            raise ToolError("unknown_field", f"unknown filter field '{f}'. Fields: {', '.join(cols)}")
        if isinstance(cond, dict):
            bad = set(cond) - OPS
            if bad or not cond:
                raise ToolError("invalid_filter", f"filter operators must be among {sorted(OPS)}")
            if "in" in cond and not isinstance(cond["in"], list):
                raise ToolError("invalid_filter", "'in' requires a list")


def _project(state: WorldState, table: str, row: dict, fields: Optional[list[str]]) -> dict:
    restricted = set(state.restricted_fields.get(table, []))
    names = fields if fields else [c for c in row if c not in restricted]
    return {k: row[k] for k in names}


def _check_fields(cols: list[str], fields: Optional[list[str]]):
    for f in fields or []:
        if f not in cols:
            raise ToolError("unknown_field", f"unknown field '{f}'. Fields: {', '.join(cols)}")


class QueryRecordsArgs(ToolArgs):
    table: str
    filters: dict[str, Any] = Field(default_factory=dict, description="field -> value, or field -> {gt,gte,lt,lte,ne,in: ...}")
    fields: Optional[list[str]] = Field(None, description="columns to return; default = all non-restricted columns")
    limit: int = Field(20, ge=1, le=MAX_LIMIT)


@tool("query_records", "database", "Query rows from a table with simple filters. Rows are returned in primary-key order.",
      SideEffect.NONE, QueryRecordsArgs, perms=("db:read",))
def query_records(state: WorldState, a: QueryRecordsArgs):
    rows = _table(state, a.table)
    cols = _columns(rows)
    _validate_filters(cols, a.filters)
    _check_fields(cols, a.fields)
    matched = [(k, r) for k, r in sorted(rows.items()) if _match(r, a.filters)]
    return {"table": a.table, "total_matches": len(matched), "count": min(len(matched), a.limit),
            "truncated": len(matched) > a.limit,
            "rows": [_project(state, a.table, r, a.fields) for _, r in matched[:a.limit]]}


class GetRecordArgs(ToolArgs):
    table: str
    record_id: str
    fields: Optional[list[str]] = None


@tool("get_record", "database", "Fetch one record by primary key.", SideEffect.NONE, GetRecordArgs, perms=("db:read",))
def get_record(state: WorldState, a: GetRecordArgs):
    rows = _table(state, a.table)
    _check_fields(_columns(rows), a.fields)
    if a.record_id not in rows:
        raise ToolError("not_found", f"{a.table} record '{a.record_id}' not found")
    return {"table": a.table, "record": _project(state, a.table, rows[a.record_id], a.fields)}


class UpdateRecordArgs(ToolArgs):
    table: str
    record_id: str
    changes: dict[str, Any] = Field(min_length=1)


@tool("update_record", "database", "Update fields of one record.", SideEffect.INTERNAL, UpdateRecordArgs, perms=("db:write",))
def update_record(state: WorldState, a: UpdateRecordArgs):
    rows = _table(state, a.table)
    if a.record_id not in rows:
        raise ToolError("not_found", f"{a.table} record '{a.record_id}' not found")
    allowed = MUTABLE.get(a.table, set())
    for f, v in a.changes.items():
        if f not in allowed:
            raise ToolError("field_not_updatable", f"field '{f}' cannot be updated. Updatable: {', '.join(sorted(allowed))}")
        if (a.table, f) in ENUMS and v not in ENUMS[(a.table, f)]:
            raise ToolError("invalid_value", f"'{f}' must be one of {sorted(ENUMS[(a.table, f)])}")
        if f == "amount" and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ToolError("invalid_value", "'amount' must be a non-negative number")
        if f != "amount" and not isinstance(v, str):
            raise ToolError("invalid_value", f"'{f}' must be a string")
    row = rows[a.record_id]
    old = {f: row[f] for f in a.changes}
    row.update(a.changes)
    state.commit("update_record", f"updated {a.table}/{a.record_id}", table=a.table, record_id=a.record_id,
                 old=old, new=dict(a.changes))
    return {"table": a.table, "record_id": a.record_id, "updated_fields": sorted(a.changes), "status": "updated"}


class DeleteRecordArgs(ToolArgs):
    table: str
    record_id: str


@tool("delete_record", "database", "Delete one record by primary key (simulated, recorded in the audit log).",
      SideEffect.DESTRUCTIVE, DeleteRecordArgs, perms=("db:delete",))
def delete_record(state: WorldState, a: DeleteRecordArgs):
    rows = _table(state, a.table)
    if a.record_id not in rows:
        raise ToolError("not_found", f"{a.table} record '{a.record_id}' not found")
    if a.table == "customers":
        refs = [t for t in ("invoices", "orders", "support_cases")
                if any(r["customer_id"] == a.record_id for r in state.database.get(t, {}).values())]
        if refs:
            raise ToolError("foreign_key_violation", f"customer '{a.record_id}' is referenced by: {', '.join(refs)}")
    deleted = rows.pop(a.record_id)
    state.commit("delete_record", f"deleted {a.table}/{a.record_id}", table=a.table, record_id=a.record_id, deleted=deleted)
    return {"table": a.table, "record_id": a.record_id, "status": "deleted"}
