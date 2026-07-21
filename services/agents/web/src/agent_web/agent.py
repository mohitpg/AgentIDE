"""
Web Search agent — search → fetch/extract → synthesize with citations.

Like the RAG agent (and unlike the LLM-only Code/Summarizer agents), this agent
overrides `run`: it gathers grounding context from the live web, then answers with
inline `[n]` citations and a deterministic `Sources:` footer of URLs — the same
citation contract the UI already renders for RAG.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from agent_common.base import BaseAgent
from agent_common.config import get_agent_settings
from agent_common.llm import build_chat_model
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from agent_web.search import (
    SearchResult,
    default_search_client,
    extract_urls,
    fetch_url,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from agent_web.search import WebSearchClient

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"

# Sentinel distinguishing "caller didn't pass a search_client" (→ build the
# configured default) from an explicit `None` (→ no client, i.e. not configured).
_UNSET = object()

_NOT_CONFIGURED = (
    "Web search isn't configured. Set SERPER_API_KEY in the environment so I can "
    "query the web, then ask again."
)
_NO_RESULTS = (
    "I couldn't find any web results for that. Try rephrasing the question or "
    "making it more specific."
)
_NO_URLS = (
    "I didn't find a URL to fetch in your message. Include an http(s) link and I'll "
    "read the page for you."
)


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


def format_results(results: list[SearchResult]) -> tuple[str, str]:
    """Render results for the prompt and a matching `Sources:` footer.

    Returns ``(context, sources)`` using the same 1-based numbering, so the model's
    inline `[n]` citations line up with the footer entries (mirrors the RAG agent).
    """
    context_parts: list[str] = []
    source_parts: list[str] = []
    for n, r in enumerate(results, start=1):
        context_parts.append(f"[{n}] {r.title} ({r.url})\n{r.content}")
        source_parts.append(f"[{n}] {r.title} — {r.url}")
    return "\n\n".join(context_parts), "Sources:\n" + "\n".join(source_parts)


class WebAgent(BaseAgent):
    id = "web"
    name = "Web Search"
    description = "Searches the web and answers with cited sources."

    def __init__(
        self,
        *,
        search_client: WebSearchClient | None | object = _UNSET,
        chat_model: BaseChatModel | None = None,
        max_results: int | None = None,
        fetch_chars: int | None = None,
        fetch_timeout: float | None = None,
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("url", "http://localhost:8105")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        s = get_agent_settings()
        # Omitting search_client → build the configured default (itself None when no
        # API key is set); passing None explicitly forces the not-configured path;
        # tests inject a fake to stay offline.
        self._search_client: WebSearchClient | None = (
            default_search_client(s.serper_api_key, timeout=s.web_fetch_timeout_seconds)
            if search_client is _UNSET
            else search_client  # type: ignore[assignment]
        )
        self._chat_model = chat_model
        self._max_results = max_results or s.web_search_max_results
        self._fetch_chars = fetch_chars or s.web_fetch_chars
        self._fetch_timeout = fetch_timeout or s.web_fetch_timeout_seconds

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    def default_skill_id(self) -> str:
        # `search` is the primary path; a bare query with no skill should search,
        # not fall through to `fetch` (which sorts first and needs a URL).
        return "search"

    # ── page fetching (injectable seam for tests) ──────────────────────────────
    async def _fetch(self, url: str) -> str:
        return await fetch_url(
            url, timeout=self._fetch_timeout, max_chars=self._fetch_chars
        )

    async def _gather_fetched(self, urls: list[str]) -> list[SearchResult]:
        """Fetch each URL to a `SearchResult` (best-effort — a dead link is skipped)."""
        gathered: list[SearchResult] = []
        for url in urls:
            try:
                content = await self._fetch(url)
            except Exception:  # a single bad URL shouldn't abort the turn
                logger.warning("web: failed to fetch %s", url, exc_info=True)
                continue
            if content:
                gathered.append(SearchResult(title=url, url=url, content=content))
        return gathered

    # ── synthesis (shared by both skills) ──────────────────────────────────────
    async def _synthesize(
        self, skill_id: str, question: str, results: list[SearchResult]
    ) -> AsyncIterator[str]:
        context, sources = format_results(results)
        skill = self.get_skill(skill_id) or self._skills[self.default_skill_id()]
        model = self._chat_model or build_chat_model(self._llm_config, streaming=True)
        user_prompt = (
            f"Web results:\n\n{context}\n\n"
            f"Request: {question}\n\n"
            "Answer using ONLY the results above. Cite the results you use inline as "
            "[n]. If the results don't cover the request, say so."
        )
        messages = [SystemMessage(content=skill.prompt), HumanMessage(content=user_prompt)]
        async for chunk in model.astream(messages):
            if isinstance(chunk, AIMessageChunk):
                piece = _text(chunk.content)
                if piece:
                    yield piece
        # Deterministic footer so the UI always shows what grounded the answer.
        yield "\n\n" + sources

    # ── execution ──────────────────────────────────────────────────────────────
    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        if skill_id == "fetch":
            urls = extract_urls(user_input)
            if not urls:
                yield _NO_URLS
                return
            results = await self._gather_fetched(urls)
            if not results:
                yield _NO_RESULTS
                return
            async for piece in self._synthesize("fetch", user_input, results):
                yield piece
            return

        # Default skill: `search`.
        if self._search_client is None:
            yield _NOT_CONFIGURED
            return
        results = await self._search_client.search(
            user_input, max_results=self._max_results
        )
        if not results:
            yield _NO_RESULTS
            return
        async for piece in self._synthesize("search", user_input, results):
            yield piece
