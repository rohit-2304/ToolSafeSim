"""Schemas for hand/AI-written fixture content (content/*.yaml). Strict, with realism guards."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

ALLOWED_TLDS = (".test", ".example")
PLACEHOLDERS = re.compile(r"lorem ipsum|draft content for|todo|tbd|xxx|placeholder text|\{[a-z_]+\}", re.I)
INJECTION_HINTS = re.compile(
    r"ignore (all |your )?(previous|prior) instructions|system override|as an ai|forward .* to .*@", re.I)


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def _text_checks(cls, v: str, min_len: int, label: str) -> str:
        v = v.strip("\n") if label == "x" else v
        if len(v.strip()) < min_len:
            raise ValueError(f"{label} too short (<{min_len} chars)")
        if PLACEHOLDERS.search(v):
            raise ValueError(f"{label} contains placeholder/template text")
        if INJECTION_HINTS.search(v):
            raise ValueError(f"{label} looks like an injection; put attacks in fixtures/injections.py, not clean content")
        return v


class EmailContent(_C):
    sender_name: str
    address: str
    subject: str
    body: str
    days_ago: float

    @field_validator("address")
    @classmethod
    def _addr(cls, v):
        if not v.lower().endswith(ALLOWED_TLDS) or "@" not in v:
            raise ValueError("address must be on a .test/.example domain")
        return v

    @field_validator("body")
    @classmethod
    def _body(cls, v):
        return cls._text_checks(v, 40, "body")

    @field_validator("days_ago")
    @classmethod
    def _days(cls, v):
        if not 0 <= v <= 120:
            raise ValueError("days_ago must be within 0..120")
        return v


class SupportCaseContent(_C):
    subject: str
    description: str
    status: Literal["open", "pending", "resolved"]
    team: Literal["tier1", "tier2", "billing"]
    private_notes: str
    customer_text: str

    @field_validator("description")
    @classmethod
    def _d(cls, v):
        return cls._text_checks(v, 40, "description")


class WebArticleContent(_C):
    topic: str
    title: str
    domain: str
    author: str
    published: str
    body: str

    @field_validator("domain")
    @classmethod
    def _dom(cls, v):
        if not v.lower().endswith(ALLOWED_TLDS):
            raise ValueError("domain must be .test/.example")
        return v

    @field_validator("published")
    @classmethod
    def _pub(cls, v):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError("published must be YYYY-MM-DD")
        return v

    @field_validator("body")
    @classmethod
    def _body(cls, v):
        return cls._text_checks(v, 120, "body")


class FileDocContent(_C):
    name: str
    sensitivity: Literal["public", "internal", "confidential"]  # restricted files are hand-anchored in files.py
    content: str

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not re.fullmatch(r"[a-z0-9_\-]+", v):
            raise ValueError("name: lowercase letters, digits, _ or - only (no extension)")
        return v

    @field_validator("content")
    @classmethod
    def _c(cls, v):
        return cls._text_checks(v, 40, "content")


CONTENT_FILES: dict[str, type[_C]] = {
    "emails.yaml": EmailContent,
    "support_cases.yaml": SupportCaseContent,
    "web_articles.yaml": WebArticleContent,
    "file_docs.yaml": FileDocContent,
}
