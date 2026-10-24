"""Content library loader: reads and validates content/*.yaml and exposes the structures the
fixture generators use. To add content, edit/append YAML files in simulation/content/ (see docs/CONTENT_GUIDE.md)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .content_schema import (CONTENT_FILES, EmailContent, FileDocContent, SupportCaseContent,
                             WebArticleContent)

CONTENT_DIR = Path(__file__).resolve().parents[3] / "content"


def load_models(name: str, content_dir: Path = CONTENT_DIR) -> list:
    model = CONTENT_FILES[name]
    rows = yaml.safe_load((content_dir / name).read_text(encoding="utf-8")) or []
    return [model.model_validate(r) for r in rows]


def _emails():
    return [(m.sender_name, m.address, m.subject, m.body, m.days_ago) for m in load_models("emails.yaml")]


def _cases():
    return [(m.subject, m.description, m.status, m.team, m.private_notes, m.customer_text)
            for m in load_models("support_cases.yaml")]


def _articles():
    return {m.topic: (m.title, m.domain, m.author, m.published, m.body) for m in load_models("web_articles.yaml")}


def _docs():
    return [(m.name, m.sensitivity, m.content) for m in load_models("file_docs.yaml")]


EMAILS = _emails()
SUPPORT_CASES = _cases()
WEB_ARTICLES = _articles()
FILE_DOCS = _docs()
