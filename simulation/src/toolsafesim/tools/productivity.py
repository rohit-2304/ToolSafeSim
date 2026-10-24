"""Productivity tools: search_emails, read_email, search_calendar, create_event, draft_email, send_email."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pydantic import Field, field_validator

from ..schemas import CalendarEvent, Draft, SentMessage, SideEffect
from ..state import WorldState
from .base import ToolArgs, ToolError, clock, snippet, tool

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
FOLDERS = ("inbox", "archive", "drafts", "sent")


def _emails(v):
    if isinstance(v, str):
        v = [v]
    for a in v:
        if not EMAIL_RE.match(a):
            raise ValueError(f"invalid email address: {a!r}")
    return v


def _parse_iso(s: str) -> datetime:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("invalid_datetime", f"'{s}' is not an ISO-8601 datetime (e.g. 2026-10-27T14:00:00Z)")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- search_emails
class SearchEmailsArgs(ToolArgs):
    query: str = Field("", max_length=200, description="Words to match in subject, body or sender (all must match). Empty = list folder.")
    folder: str = Field("inbox", description="inbox | archive | drafts | sent")
    limit: int = Field(10, ge=1, le=50)


@tool("search_emails", "productivity", "Search emails in a folder (inbox, archive, drafts, sent). Returns matching messages, newest first.",
      SideEffect.NONE, SearchEmailsArgs, perms=("email:read",))
def search_emails(state: WorldState, a: SearchEmailsArgs):
    if a.folder not in FOLDERS:
        raise ToolError("invalid_folder", f"folder must be one of {', '.join(FOLDERS)}")
    rows = []
    if a.folder in ("inbox", "archive"):
        rows = [(e.email_id, e.sender_address, e.subject, e.body, e.received_at)
                for e in state.emails.values() if e.folder == a.folder]
    elif a.folder == "drafts":
        rows = [(d.draft_id, state.user_email, d.subject, d.body, d.created_at) for d in state.drafts.values() if d.status == "draft"]
    else:
        rows = [(s.sent_id, state.user_email, s.subject, s.body, s.sent_at) for s in state.sent_messages.values()]
    toks = a.query.lower().split()
    hits = [r for r in rows if all(t in f"{r[1]} {r[2]} {r[3]}".lower() for t in toks)]
    hits.sort(key=lambda r: (r[4], r[0]), reverse=True)
    return {"folder": a.folder, "count": len(hits[:a.limit]), "total_matches": len(hits),
            "results": [{"id": r[0], "from": r[1], "subject": r[2], "date": r[4], "snippet": snippet(r[3])}
                        for r in hits[:a.limit]]}


# --------------------------------------------------------------------------- read_email
class ReadEmailArgs(ToolArgs):
    email_id: str = Field(description="Email id (em_*), draft id (draft_*) or sent id (sent_*)")


@tool("read_email", "productivity", "Read the full content of an email, draft or sent message by id.",
      SideEffect.NONE, ReadEmailArgs, perms=("email:read",))
def read_email(state: WorldState, a: ReadEmailArgs):
    if a.email_id in state.emails:
        e = state.emails[a.email_id]
        return {k: v for k, v in e.agent_view().items() if k != "read_status"}
    if a.email_id in state.drafts:
        d = state.drafts[a.email_id]
        return {"draft_id": d.draft_id, "to": d.to, "cc": d.cc, "subject": d.subject, "body": d.body, "status": d.status}
    if a.email_id in state.sent_messages:
        return state.sent_messages[a.email_id].model_dump(mode="json")
    raise ToolError("not_found", f"no email, draft or sent message with id '{a.email_id}'")


# --------------------------------------------------------------------------- search_calendar
class SearchCalendarArgs(ToolArgs):
    start_date: str = Field(description="YYYY-MM-DD (inclusive)")
    end_date: str = Field(description="YYYY-MM-DD (inclusive)")


@tool("search_calendar", "productivity", "List calendar events overlapping a date range (inclusive), ordered by start time.",
      SideEffect.NONE, SearchCalendarArgs, perms=("calendar:read",))
def search_calendar(state: WorldState, a: SearchCalendarArgs):
    try:
        s, e = date.fromisoformat(a.start_date), date.fromisoformat(a.end_date)
    except ValueError:
        raise ToolError("invalid_date", "dates must be YYYY-MM-DD")
    if e < s:
        raise ToolError("invalid_range", "end_date is before start_date")
    if (e - s).days > 62:
        raise ToolError("range_too_large", "range may not exceed 62 days")
    lo = datetime(s.year, s.month, s.day, tzinfo=timezone.utc)
    hi = datetime(e.year, e.month, e.day, tzinfo=timezone.utc) + timedelta(days=1)
    evs = [ev for ev in state.calendar_events.values()
           if _parse_iso(ev.start) < hi and _parse_iso(ev.end) > lo and ev.status != "cancelled"]
    evs.sort(key=lambda ev: (ev.start, ev.event_id))
    return {"count": len(evs), "events": [
        {"event_id": ev.event_id, "title": ev.title, "description": ev.description, "start": ev.start,
         "end": ev.end, "attendees": ev.attendees, "organizer": ev.organizer} for ev in evs]}


# --------------------------------------------------------------------------- create_event
class CreateEventArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(description="ISO-8601 start, e.g. 2026-10-27T14:00:00Z")
    end: str = Field(description="ISO-8601 end")
    attendees: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field("", max_length=2000)

    @field_validator("attendees", mode="before")
    @classmethod
    def _check_attendees(cls, v):
        return _emails(v)


@tool("create_event", "productivity", "Create a calendar event and (simulated) invite the attendees.",
      SideEffect.EXTERNAL, CreateEventArgs, perms=("calendar:write",))
def create_event(state: WorldState, a: CreateEventArgs):
    s, e = _parse_iso(a.start), _parse_iso(a.end)
    if e <= s:
        raise ToolError("invalid_range", "event end must be after start")
    if (e - s) > timedelta(hours=24):
        raise ToolError("invalid_range", "event may not be longer than 24 hours")
    eid = state.next_id("evt")
    organizer = state.user_email or "user"
    att = list(dict.fromkeys([organizer, *a.attendees]))
    state.calendar_events[eid] = CalendarEvent(
        event_id=eid, title=a.title, description=a.description, start=a.start, end=a.end,
        organizer=organizer, attendees=att, source="agent")
    state.commit("create_event", f"created {eid}", event_id=eid, attendees=att)
    return {"event_id": eid, "status": "created", "attendees": att, "invites": "simulated"}


# --------------------------------------------------------------------------- draft_email
class DraftEmailArgs(ToolArgs):
    to: list[str] = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    cc: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("to", "cc", mode="before")
    @classmethod
    def _check_addresses(cls, v):
        return _emails(v)


@tool("draft_email", "productivity", "Create an email draft (not sent). Returns the draft id.",
      SideEffect.INTERNAL, DraftEmailArgs, perms=("email:draft",))
def draft_email(state: WorldState, a: DraftEmailArgs):
    did = state.next_id("draft")
    state.drafts[did] = Draft(draft_id=did, to=a.to, cc=a.cc, subject=a.subject, body=a.body, created_at=clock(state))
    state.commit("draft_email", f"created {did}", draft_id=did, to=a.to)
    return {"draft_id": did, "status": "draft", "to": a.to, "subject": a.subject}


# --------------------------------------------------------------------------- send_email
class SendEmailArgs(ToolArgs):
    draft_id: str


@tool("send_email", "productivity", "Send an existing draft. The message leaves the mailbox (simulated).",
      SideEffect.EXTERNAL, SendEmailArgs, perms=("email:send",))
def send_email(state: WorldState, a: SendEmailArgs):
    d = state.drafts.get(a.draft_id)
    if d is None:
        raise ToolError("not_found", f"draft '{a.draft_id}' does not exist")
    if d.status != "draft":
        raise ToolError("conflict", f"draft '{a.draft_id}' is already {d.status}")
    sid = state.next_id("sent")
    state.sent_messages[sid] = SentMessage(sent_id=sid, draft_id=d.draft_id, to=d.to, cc=d.cc, subject=d.subject,
                                           body=d.body, sent_at=clock(state))
    d.status = "sent"
    state.commit("send_email", f"sent {a.draft_id} as {sid}", draft_id=a.draft_id, sent_id=sid, to=d.to)
    return {"sent_id": sid, "status": "simulated_sent", "to": d.to}
