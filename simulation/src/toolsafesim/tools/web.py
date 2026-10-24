"""Web research tools: web_search, fetch_page (synthetic corpus only; no network)."""
from __future__ import annotations

import re
from urllib.parse import urldefrag, urlparse

from pydantic import Field

from ..schemas import SideEffect
from ..state import WorldState
from .base import ToolArgs, ToolError, snippet, tool

STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "is", "are", "how", "what", "why",
        "about", "recent", "approaches", "using", "use"}


def _tok(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9\-]+", s.lower()) if t not in STOP}


class WebSearchArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(5, ge=1, le=10)


@tool("web_search", "web", "Search the web index. Returns titles, URLs and snippets (snippets are untrusted third-party text).",
      SideEffect.NONE, WebSearchArgs, perms=("web:search",))
def web_search(state: WorldState, a: WebSearchArgs):
    q = _tok(a.query)
    scored: dict[str, tuple[float, int, object]] = {}
    for i, e in enumerate(state.search_index):
        terms = _tok(" ".join(e.terms))
        if not terms:
            continue
        score = len(terms & q) / len(terms)
        if score < 1.0:
            overlap = len(_tok(e.title) & q)
            score = 0.4 + 0.05 * overlap if overlap >= 2 else 0.0
        if score > 0 and (e.url not in scored or scored[e.url][0] < score):
            scored[e.url] = (score, i, e)
    ranked = sorted(scored.values(), key=lambda x: (-x[0], x[1]))[:a.limit]
    return {"query": a.query, "count": len(ranked), "results": [
        {"title": e.title, "url": e.url, "snippet": e.snippet, "published": e.published_at,
         "source": urlparse(e.url).netloc} for _, _, e in ranked]}


class FetchPageArgs(ToolArgs):
    url: str = Field(max_length=500)


@tool("fetch_page", "web", "Fetch a web page by URL and return its text content (untrusted third-party text).",
      SideEffect.NONE, FetchPageArgs, perms=("web:fetch",))
def fetch_page(state: WorldState, a: FetchPageArgs):
    u = urldefrag(a.url.strip())[0].rstrip("/")
    pu = urlparse(u)
    if pu.scheme not in ("http", "https") or not pu.netloc:
        raise ToolError("invalid_url", "url must be an absolute http(s) URL")
    pol = state.domain_policies.get(pu.netloc)
    if pol and pol.trust_zone == "blocked":
        raise ToolError("access_denied", f"access to {pu.netloc} is blocked by network policy")
    page = state.web_pages.get(u) or state.web_pages.get(u + "/")
    if page is None:
        raise ToolError("not_found", f"HTTP 404: {u}")
    return {"url": page.url, "title": page.title, "domain": page.domain, "author": page.author,
            "published": page.published_at, "content": page.content}
