"""Fixture validation: referential integrity, realism invariants and secret/PII scanning."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..state import WorldState
from .loader import FIXTURE_DIR, load_pack, load_pack_dict

EMAIL_RE = re.compile(r"(?<!\\)[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
SECRET_PATTERNS = {
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "openai_like": re.compile(r"sk-(?!demo)[A-Za-z0-9]{20,}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
}
ALLOWED_TLDS = (".test", ".example")
SAFE_CRED = re.compile(r"(demo|NOT_|example|placeholder|localhost)", re.I)


def scan_secrets(text: str, label: str) -> list[str]:
    issues = []
    for name, pat in SECRET_PATTERNS.items():
        if pat.search(text):
            issues.append(f"{label}: possible real secret ({name})")
    for m in EMAIL_RE.finditer(text):
        if not m.group(1).lower().endswith(ALLOWED_TLDS):
            issues.append(f"{label}: non-synthetic email domain {m.group(0)}")
    for m in re.finditer(r"(?:token|secret|key|password)\s*[=:]\s*\"?([A-Za-z0-9_\-]{8,})", text, re.I):
        if not SAFE_CRED.search(m.group(1)):
            issues.append(f"{label}: credential-like value without demo marker: {m.group(1)[:12]}…")
    return issues


# ---- per-pack checks -------------------------------------------------------
def _productivity(st: WorldState) -> list[str]:
    out = []
    for e in st.emails.values():
        for ref in re.findall(r"\bdoc_[a-z0-9_]+", e.body):
            if ref not in st.documents:
                out.append(f"email {e.email_id} references missing {ref}")
    for a in st.protected_assets.values():
        rid = a.resource_id
        if rid not in st.documents:
            out.append(f"protected asset {a.asset_id} -> missing {rid}")
    for ev in st.calendar_events.values():
        if datetime.fromisoformat(ev.start.replace("Z", "+00:00")) >= datetime.fromisoformat(ev.end.replace("Z", "+00:00")):
            out.append(f"event {ev.event_id} start >= end")
    inv = [e for e in st.emails.values() if "invoice" in e.subject.lower()]
    if max(inv, key=lambda e: e.received_at).email_id != "em_invoice_01":
        out.append("latest invoice email is not em_invoice_01")
    # free 30-min slot tomorrow afternoon (14:00-15:00)
    for ev in st.calendar_events.values():
        if "2026-10-23" in ev.start and ev.start[11:16] < "15:00" and ev.end[11:16] > "14:00":
            out.append(f"no free slot tomorrow 14-15: conflicts with {ev.event_id}")
    if len({e.email_id for e in st.emails.values() if not e.email_id.startswith("em_") or e.email_id[3:].isdigit()}) < 0.3 * len(st.emails):
        out.append("distractor emails < 30%")
    for need in ("draft_approved_01",):
        if need not in st.drafts:
            out.append(f"missing {need}")
    return out


def _billing(st: WorldState) -> list[str]:
    out, db, now = [], st.database, datetime.fromisoformat(st.now.replace("Z", "+00:00")).date()
    for t, fields in st.restricted_fields.items():
        for row in db[t].values():
            for f in fields:
                if f not in row:
                    out.append(f"{t} row missing restricted field {f}")
                    break
    for inv in db["invoices"].values():
        if inv["customer_id"] not in db["customers"]:
            out.append(f"invoice {inv['invoice_id']} bad customer")
        due = datetime.fromisoformat(inv["due_date"]).date()
        if inv["status"] == "overdue" and due >= now:
            out.append(f"{inv['invoice_id']} overdue but due {due}")
        if inv["status"] == "open" and due < now:
            out.append(f"{inv['invoice_id']} open but past due")
    for t in ("orders", "support_cases"):
        for row in db[t].values():
            if row["customer_id"] not in db["customers"]:
                out.append(f"{t} bad customer")
    if "INV-204" not in db["invoices"] or db["invoices"]["INV-204"]["status"] != "overdue":
        out.append("anchor INV-204 missing/not overdue")
    return out


def _repo(st: WorldState) -> list[str]:
    out = []
    for need in ("/repo/invoice-service/.env", "/repo/invoice-service/config/production.toml",
                 "/repo/invoice-service/issues/issue_42.md", "/repo/invoice-service/src/invoices/service.py"):
        if need not in st.filesystem:
            out.append(f"missing file {need}")
    for a in st.protected_assets.values():
        if a.resource_id not in st.filesystem:
            out.append(f"protected asset {a.asset_id} missing file")
    if "/repo/invoice-service/tests" not in st.test_results:
        out.append("missing test_results for tests dir")
    return out


def _web(st: WorldState) -> list[str]:
    out = []
    for s in st.search_index:
        if s.url not in st.web_pages:
            out.append(f"search entry -> missing page {s.url}")
    for p in st.web_pages.values():
        if p.domain in st.domain_policies and st.domain_policies[p.domain].trust_zone == "blocked":
            out.append(f"page on blocked domain {p.domain}")
    if not st.private_context:
        out.append("no private_context")
    return out


def _files(st: WorldState) -> list[str]:
    out = []
    for perm in st.permissions:
        if perm.resource_id not in st.filesystem:
            out.append(f"permission -> missing resource {perm.resource_id}")
        if perm.principal != "*" and perm.principal not in st.principals:
            out.append(f"permission principal {perm.principal} unknown")
    for a in st.protected_assets.values():
        if a.resource_id not in st.filesystem:
            out.append(f"protected asset {a.asset_id} missing file")
    for f in st.filesystem.values():
        for p in f.shareable_with:
            if p not in st.principals:
                out.append(f"{f.path} shareable_with unknown {p}")
    return out


CHECKS: dict[str, Callable[[WorldState], list[str]]] = {
    "productivity_demo_v1": _productivity, "billing_demo_v1": _billing, "repo_demo_v1": _repo,
    "web_demo_v1": _web, "files_demo_v1": _files,
}


def validate_all(fixture_dir: Path = FIXTURE_DIR) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {}
    for name, check in CHECKS.items():
        issues = check(load_pack(name, fixture_dir=fixture_dir))
        issues += scan_secrets(json.dumps(load_pack_dict(name, fixture_dir)), name)
        report[name] = issues
    inj = (fixture_dir / "injections.json").read_text(encoding="utf-8")
    report["injections"] = scan_secrets(inj, "injections")
    return report


if __name__ == "__main__":
    rep = validate_all()
    bad = {k: v for k, v in rep.items() if v}
    print(json.dumps(bad or "OK", indent=2))
    raise SystemExit(1 if bad else 0)
