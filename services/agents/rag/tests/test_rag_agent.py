"""
RAG agent tests — no models, no database.

Card/skills are exercised directly; `run` is exercised with an injected fake
pipeline (scripted hits), a fake async session, and a fake chat model, so none of
the heavy embedder/reranker/LLM dependencies or Postgres are needed.
"""

from __future__ import annotations

import uuid

import pytest
from agent_common.scope import RequestScope, reset_scope, set_scope
from agent_rag.agent import RagAgent, format_context
from langchain_core.messages import AIMessageChunk
from rag import RagConfig, RagScope, SearchHit

SCOPE = RequestScope(user_id=uuid.uuid4(), project_id=uuid.uuid4())


@pytest.fixture(autouse=True)
def request_scope():
    """Run each test as if the supervisor sent SCOPE (what the executor does)."""
    token = set_scope(SCOPE)
    yield
    reset_scope(token)


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePipeline:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.config = RagConfig()
        self.scopes: list[RagScope] = []

    async def search(self, session, query, *, scope, top_k, candidate_k):  # type: ignore[no-untyped-def]
        self.scopes.append(scope)
        return self._hits


class FakeChatModel:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        for p in self._pieces:
            yield AIMessageChunk(content=p)


def _hit(filename: str, idx: int, content: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename=filename,
        chunk_index=idx,
        content=content,
        score=1.0,
        sources=["vector"],
    )


def _agent(hits: list[SearchHit], pieces: list[str] | None = None) -> RagAgent:
    return RagAgent(
        pipeline=FakePipeline(hits),
        chat_model=FakeChatModel(pieces or []),
        session_factory=lambda: FakeSession(),
    )


async def _collect(agent: RagAgent, skill: str, text: str) -> str:
    return "".join([chunk async for chunk in agent.run(skill, text)])


def test_card_and_skills_load() -> None:
    agent = RagAgent(session_factory=lambda: FakeSession())
    assert agent.id == "rag"
    assert agent.card.name == "RAG"
    assert set(agent.skills) == {"answer", "retrieve"}


def test_format_context_numbers_passages_and_sources() -> None:
    context, sources = format_context([_hit("a.txt", 0, "alpha"), _hit("b.pdf", 2, "beta")])
    assert "[1] (a.txt #0)" in context and "alpha" in context
    assert "[2] (b.pdf #2)" in context and "beta" in context
    assert "Sources:" in sources
    assert "[1] a.txt (chunk 0)" in sources
    assert "[2] b.pdf (chunk 2)" in sources


async def test_answer_streams_text_with_citation_footer() -> None:
    hits = [_hit("doc.txt", 0, "the sky is blue")]
    out = await _collect(_agent(hits, pieces=["The sky ", "is blue [1]."]), "answer", "color?")
    assert "The sky is blue [1]." in out
    assert "Sources:" in out
    assert "[1] doc.txt (chunk 0)" in out


async def test_retrieve_returns_passages_without_llm() -> None:
    hits = [_hit("doc.txt", 0, "verbatim passage text")]
    out = await _collect(_agent(hits), "retrieve", "find it")
    assert "verbatim passage text" in out
    # retrieve does not synthesise, so no Sources footer is appended
    assert "Sources:" not in out


async def test_no_hits_returns_guidance() -> None:
    out = await _collect(_agent([]), "answer", "anything")
    assert "couldn't find anything relevant" in out


async def test_search_is_scoped_to_the_callers_project() -> None:
    pipeline = FakePipeline([_hit("doc.txt", 0, "x")])
    agent = RagAgent(pipeline=pipeline, session_factory=lambda: FakeSession())
    await _collect(agent, "retrieve", "q")
    assert pipeline.scopes == [RagScope(user_id=SCOPE.user_id, project_id=SCOPE.project_id)]


async def test_no_scope_searches_nothing(request_scope) -> None:
    pipeline = FakePipeline([_hit("doc.txt", 0, "secret")])
    agent = RagAgent(pipeline=pipeline, session_factory=lambda: FakeSession())
    token = set_scope(None)
    try:
        out = await _collect(agent, "retrieve", "q")
    finally:
        reset_scope(token)
    assert "secret" not in out
    assert pipeline.scopes == []  # never queried
