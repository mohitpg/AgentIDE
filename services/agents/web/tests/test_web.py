"""
Web Search agent tests — no models, no network.

Card/skills are exercised directly; `run` is exercised with an injected fake
search client (scripted results), an injected page fetcher, and a fake chat model,
so neither the Tavily API, real HTTP, nor an LLM is needed.
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from agent_web.agent import WebAgent, format_results
from agent_web.search import SearchResult, extract_urls, html_to_text


class FakeSearchClient:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.last_query: str | None = None
        self.last_max: int | None = None

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.last_query = query
        self.last_max = max_results
        return self._results


class FakeChatModel:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces
        self.last_messages: list | None = None

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        self.last_messages = messages
        for p in self._pieces:
            yield AIMessageChunk(content=p)


def _result(title: str, url: str, content: str) -> SearchResult:
    return SearchResult(title=title, url=url, content=content)


def _agent(
    *,
    results: list[SearchResult] | None = None,
    pieces: list[str] | None = None,
    search_client: object | None = "unset",
) -> WebAgent:
    client = FakeSearchClient(results or []) if search_client == "unset" else search_client
    return WebAgent(
        search_client=client,  # type: ignore[arg-type]
        chat_model=FakeChatModel(pieces or []),
    )


async def _collect(agent: WebAgent, skill: str, text: str) -> str:
    return "".join([chunk async for chunk in agent.run(skill, text)])


# ── pure helpers ───────────────────────────────────────────────────────────────
def test_card_and_skills_load() -> None:
    agent = _agent()
    assert agent.id == "web"
    assert agent.card.name == "Web Search"
    assert set(agent.skills) == {"search", "fetch"}


def test_default_skill_is_search() -> None:
    assert _agent().default_skill_id() == "search"


def test_extract_urls_dedupes_and_strips_punctuation() -> None:
    urls = extract_urls("see https://a.com/x, and https://a.com/x again, plus https://b.io.")
    assert urls == ["https://a.com/x", "https://b.io"]


def test_html_to_text_strips_markup_and_scripts() -> None:
    html = "<html><head><title>t</title></head><body><script>bad()</script>"
    html += "<p>Hello &amp; welcome</p></body></html>"
    text = html_to_text(html)
    assert "Hello & welcome" in text
    assert "bad()" not in text
    assert "<" not in text


def test_html_to_text_truncates_to_max_chars() -> None:
    text = html_to_text("<p>" + "x" * 100 + "</p>", max_chars=10)
    assert text.endswith("…")
    assert len(text) <= 12  # 10 chars + trailing " …"


def test_format_results_numbers_context_and_sources() -> None:
    context, sources = format_results(
        [_result("Alpha", "https://a.com", "alpha body"),
         _result("Beta", "https://b.com", "beta body")]
    )
    assert "[1] Alpha (https://a.com)" in context and "alpha body" in context
    assert "[2] Beta (https://b.com)" in context
    assert "Sources:" in sources
    assert "[1] Alpha — https://a.com" in sources
    assert "[2] Beta — https://b.com" in sources


# ── search skill ───────────────────────────────────────────────────────────────
async def test_search_streams_answer_with_citation_footer() -> None:
    results = [_result("Py", "https://python.org", "Python 3.13 is current")]
    agent = _agent(results=results, pieces=["Python 3.13 ", "is current [1]."])
    out = await _collect(agent, "search", "latest python?")
    assert "Python 3.13 is current [1]." in out
    assert "Sources:" in out
    assert "[1] Py — https://python.org" in out


async def test_search_passes_query_and_max_results_to_client() -> None:
    client = FakeSearchClient([_result("t", "https://t.io", "body")])
    agent = WebAgent(search_client=client, chat_model=FakeChatModel(["ok"]))
    await _collect(agent, "search", "some question")
    assert client.last_query == "some question"
    assert client.last_max == 5  # default web_search_max_results


async def test_search_no_results_returns_guidance() -> None:
    out = await _collect(_agent(results=[]), "search", "obscure query")
    assert "couldn't find any web results" in out


async def test_search_not_configured_returns_guidance() -> None:
    # No search client (no API key configured) → graceful message, no crash.
    out = await _collect(_agent(search_client=None), "search", "anything")
    assert "isn't configured" in out


# ── fetch skill ────────────────────────────────────────────────────────────────
async def test_fetch_reads_url_and_synthesizes_with_source() -> None:
    seen: list[str] = []

    async def fake_fetch(url: str) -> str:
        seen.append(url)
        return "the page says pgvector supports HNSW"

    agent = _agent(pieces=["It supports HNSW [1]."])
    agent._fetch = fake_fetch  # type: ignore[method-assign]
    out = await _collect(agent, "fetch", "summarize https://example.com/pgvector")

    assert seen == ["https://example.com/pgvector"]
    assert "It supports HNSW [1]." in out
    assert "Sources:" in out
    assert "https://example.com/pgvector" in out


async def test_fetch_without_url_returns_guidance() -> None:
    out = await _collect(_agent(pieces=["unused"]), "fetch", "just summarize this text")
    assert "didn't find a URL" in out


async def test_fetch_all_urls_failing_returns_guidance() -> None:
    async def boom(url: str) -> str:
        raise RuntimeError("connection refused")

    agent = _agent(pieces=["unused"])
    agent._fetch = boom  # type: ignore[method-assign]
    out = await _collect(agent, "fetch", "read https://down.example.com")
    assert "couldn't find any web results" in out
