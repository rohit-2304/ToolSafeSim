"""Text helpers for provenance (taint) and data-boundary checks. Pure functions."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

_EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_DEST = re.compile(r"[a-z]+://[a-z0-9_\-/.]+")
_PATH = re.compile(r"(?<![\w.:/])/[\w.\-]+(?:/[\w.\-]+)+")
_RELPATH = re.compile(r"(?<![\w/.:])[\w.\-]+(?:/[\w.\-]+)+")
_DOTFILE = re.compile(r"(?<![\w])\.[a-z][a-z0-9_]{1,15}\b")
_RECORD = re.compile(r"\b(?:inv|c|ord|case|tkt)-\d+\b")
_OBJID = re.compile(r"\b(?:em|draft|evt|doc|ct|file)_[a-z0-9_]+\b")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def compress(text: str) -> str:
    """Lowercase, un-escape JSON newlines, collapse every non-alphanumeric run to a space."""
    t = str(text).lower().replace("\\n", " ").replace("\\t", " ")
    return _NON_ALNUM.sub(" ", t).strip()


def atoms(text: str) -> set[str]:
    """Concrete, checkable tokens: addresses, URLs, paths, record/object ids."""
    t = str(text).lower().replace("\\n", " ")
    out: set[str] = set()
    for rx in (_EMAIL, _URL, _DEST, _PATH, _RELPATH, _DOTFILE, _RECORD, _OBJID):
        out.update(m.group(0).rstrip(".,;:") for m in rx.finditer(t))
    # an email's domain-like pieces or path fragments of urls are not atoms on their own
    return {a for a in out if len(a) >= 4}


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in ([str(k)] + flatten_strings(v))]
    if isinstance(value, (list, tuple, set)):
        return [s for v in value for s in flatten_strings(v)]
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def contains(haystack_compressed: str, needle: str, min_len: int = 8) -> bool:
    n = compress(needle)
    return len(n) >= min_len and n in haystack_compressed


def sentences(text: str, min_len: int = 20) -> list[str]:
    parts: Iterable[str] = re.split(r"[\n\r]+|(?<=[.!?])\s+", str(text))
    return [p.strip() for p in parts if len(compress(p)) >= min_len]
