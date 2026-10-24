"""In-memory world state with snapshot/restore, canonical hashing and versioned mutation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas import (
    CalendarEvent, Commit, Contact, Destination, Document, DomainPolicy, Draft, Email,
    Permission, Principal, ProtectedAsset, SearchEntry, SentMessage, VFile, WebPage,
)


class WorldState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_goal: str = ""
    emails: dict[str, Email] = Field(default_factory=dict)
    drafts: dict[str, Draft] = Field(default_factory=dict)
    sent_messages: dict[str, SentMessage] = Field(default_factory=dict)
    calendar_events: dict[str, CalendarEvent] = Field(default_factory=dict)
    contacts: dict[str, Contact] = Field(default_factory=dict)
    documents: dict[str, Document] = Field(default_factory=dict)
    database: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)  # table->id->row
    filesystem: dict[str, VFile] = Field(default_factory=dict)
    web_pages: dict[str, WebPage] = Field(default_factory=dict)
    search_index: list[SearchEntry] = Field(default_factory=list)
    domain_policies: dict[str, DomainPolicy] = Field(default_factory=dict)
    private_context: dict[str, str] = Field(default_factory=dict)  # HIDDEN confidential values
    principals: dict[str, Principal] = Field(default_factory=dict)
    destinations: dict[str, Destination] = Field(default_factory=dict)
    restricted_fields: dict[str, list[str]] = Field(default_factory=dict)  # table -> fields
    commits: list[Commit] = Field(default_factory=list)
    test_results: dict[str, dict[str, Any]] = Field(default_factory=dict)  # target -> result
    now: str = "2026-10-22T09:00:00Z"  # simulated clock
    cwd: str = ""  # base directory for relative file paths
    user_email: str = ""  # mailbox owner (productivity)
    permissions: list[Permission] = Field(default_factory=list)
    protected_assets: dict[str, ProtectedAsset] = Field(default_factory=dict)
    audit_log: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)  # deterministic ID generation
    version: int = 0

    # ---- determinism helpers
    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def snapshot(self) -> "WorldState":
        return self.model_copy(deep=True)

    @classmethod
    def restore(cls, snap: "WorldState") -> "WorldState":
        return snap.model_copy(deep=True)

    def next_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}_{n:03d}"

    # ---- versioned mutation
    def commit(self, tool: str, summary: str, **extra: Any) -> int:
        """Record an accepted state transition. Must be called once per mutating handler."""
        pre = self.version
        self.version += 1
        self.audit_log.append(
            {"tool": tool, "summary": summary, "pre_version": pre,
             "post_version": self.version, **extra}
        )
        return self.version
