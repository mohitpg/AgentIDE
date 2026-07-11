"""
Web access for the Web Search agent — search API + page fetch/extract.

The chat / A2A transport carries a single text field, so the agent gets a plain
query (or a message containing URLs). This module provides the two capabilities
the agent composes on top of the LLM:

  - `WebSearchClient` — a search-API abstraction. `TavilyClient` implements it over
    Tavily's REST API; `default_search_client()` builds one from settings (or
    returns ``None`` when no key is configured, so the agent degrades gracefully).
  - `fetch_url` / `html_to_text` — fetch a page and reduce it to readable text
    (scripts/styles/markup stripped, entities unescaped, whitespace collapsed),
    with no heavy HTML dependency. Reused by the Celery `web` crawl task.

Everything network-facing takes an injectable `httpx` client so tests never touch
the network.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

# ── tunables (mirrored by AgentSettings; passed in by the agent) ───────────────
DEFAULT_MAX_RESULTS = 5
DEFAULT_FETCH_CHARS = 4000
DEFAULT_TIMEOUT_SECONDS = 15.0

_TAVILY_ENDPOINT = "https://api.tavily.com/search"

# http(s) URL up to the first whitespace / common trailing punctuation.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|head)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class SearchResult:
    """One web result: a title, its URL, and a relevant content snippet."""

    title: str
    url: str
    content: str


def extract_urls(text: str) -> list[str]:
    """Return the http(s) URLs found in ``text``, in order, de-duplicated."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall(text):
        url = match.rstrip(".,;:!?")  # trailing sentence punctuation isn't part of it
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def html_to_text(markup: str, *, max_chars: int = DEFAULT_FETCH_CHARS) -> str:
    """Reduce an HTML document to readable plain text (best-effort, dependency-free).

    Removes ``<script>``/``<style>``/``<head>`` blocks, strips remaining tags,
    unescapes HTML entities, and collapses whitespace. Truncated to ``max_chars``
    so a huge page can't blow up the LLM context.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", markup)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    return text


async def fetch_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_chars: int = DEFAULT_FETCH_CHARS,
) -> str:
    """Fetch ``url`` and return its readable text (raises on network/HTTP error)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        resp = await client.get(url, headers={"User-Agent": "AgentIDE-WebAgent/0.1"})
        resp.raise_for_status()
        return html_to_text(resp.text, max_chars=max_chars)
    finally:
        if own:
            await client.aclose()


def fetch_url_sync(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_chars: int = DEFAULT_FETCH_CHARS,
) -> str:
    """Synchronous `fetch_url`, for the Celery `web` crawl task (Celery is sync)."""
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url, headers={"User-Agent": "AgentIDE-WebAgent/0.1"})
        resp.raise_for_status()
        return html_to_text(resp.text, max_chars=max_chars)
    finally:
        if own:
            client.close()


class WebSearchClient(Protocol):
    """A search backend: turn a query into ranked `SearchResult`s."""

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]: ...


class TavilyClient:
    """`WebSearchClient` over the Tavily search API (a single POST /search)."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        own = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(_TAVILY_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if own:
                await client.aclose()
        results: list[SearchResult] = []
        for item in data.get("results", []):
            results.append(
                SearchResult(
                    title=item.get("title") or item.get("url", "result"),
                    url=item.get("url", ""),
                    content=(item.get("content") or "").strip(),
                )
            )
        return results


def default_search_client(
    api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> WebSearchClient | None:
    """Build the configured search client, or ``None`` when no API key is set."""
    if not api_key:
        return None
    return TavilyClient(api_key, timeout=timeout)
