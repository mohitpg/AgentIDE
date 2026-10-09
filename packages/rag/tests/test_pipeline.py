"""
Pipeline + retrieval tests using injected fakes — no models, no database.

Exercises the orchestration logic (chunk → embed → store; retrieve → merge →
rerank → top-k) and the pure `merge_candidates` fusion, all without downloading
the embedder/reranker or standing up Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from rag.config import RagConfig
from rag.embeddings import Vector
from rag.pipeline import RagPipeline
from rag.store import Candidate, ChunkInput, RagScope, VectorStore, merge_candidates

SCOPE = RagScope(user_id=uuid.uuid4(), project_id=uuid.uuid4())


class FakeEmbedder:
    dim = 4

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]

    def embed_query(self, text: str) -> Vector:
        return [float(len(text)), 1.0, 0.0, 0.0]


class FakeReranker:
    """Scores by how many query words appear in the chunk (substring match)."""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        terms = query.lower().split()
        return [float(sum(t in doc.lower() for t in terms)) for doc in documents]


class FakeStore:
    """In-memory stand-in for VectorStore; records ingests, scripts retrieval."""

    def __init__(self, vector_hits=None, keyword_hits=None) -> None:
        self.documents: list[dict] = []
        self.chunks: list[ChunkInput] = []
        self._vector_hits = vector_hits or []
        self._keyword_hits = keyword_hits or []
        self.search_scopes: list[RagScope] = []

    async def add_document(self, session, **kwargs) -> uuid.UUID:  # type: ignore[no-untyped-def]
        doc_id = uuid.uuid4()
        self.documents.append({"id": doc_id, **kwargs})
        return doc_id

    async def add_chunks(self, session, document_id, chunks) -> None:  # type: ignore[no-untyped-def]
        self.chunks.extend(chunks)

    async def vector_search(self, session, query_embedding, limit, scope):  # type: ignore[no-untyped-def]
        self.search_scopes.append(scope)
        return self._vector_hits[:limit]

    async def keyword_search(self, session, query, limit, scope):  # type: ignore[no-untyped-def]
        self.search_scopes.append(scope)
        return self._keyword_hits[:limit]


def _candidate(content: str, *, source: str) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="doc.txt",
        chunk_index=0,
        content=content,
        sources=[source],
        vector_score=0.9 if source == "vector" else None,
        keyword_score=0.5 if source == "keyword" else None,
    )


async def test_ingest_chunks_embeds_and_stores() -> None:
    store = FakeStore()
    pipeline = RagPipeline(
        RagConfig(chunk_tokens=10, chunk_overlap=2),
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        store=store,
    )
    data = (" ".join(f"word{i}" for i in range(25))).encode()

    result = await pipeline.ingest(
        None, scope=SCOPE, filename="notes.txt", content_type="text/plain", data=data  # type: ignore[arg-type]
    )

    assert result.num_chunks > 1
    assert result.filename == "notes.txt"
    assert len(store.chunks) == result.num_chunks
    # every stored chunk got an embedding of the embedder's dimension
    assert all(len(c.embedding) == FakeEmbedder.dim for c in store.chunks)


async def test_search_merges_and_reranks_top_k() -> None:
    vector_hits = [_candidate("alpha beta gamma", source="vector")]
    keyword_hits = [
        _candidate("the quick brown fox", source="keyword"),
        _candidate("nothing relevant here", source="keyword"),
    ]
    store = FakeStore(vector_hits=vector_hits, keyword_hits=keyword_hits)
    pipeline = RagPipeline(
        RagConfig(top_k=2, candidate_k=10),
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        store=store,
    )

    hits = await pipeline.search(None, "quick brown fox", scope=SCOPE)  # type: ignore[arg-type]

    assert len(hits) == 2  # top_k honoured
    # the chunk containing all query terms reranks first
    assert hits[0].content == "the quick brown fox"
    assert hits[0].score >= hits[1].score


async def test_search_empty_corpus_returns_nothing() -> None:
    pipeline = RagPipeline(embedder=FakeEmbedder(), reranker=FakeReranker(), store=FakeStore())
    assert await pipeline.search(None, "anything", scope=SCOPE) == []  # type: ignore[arg-type]


def test_merge_candidates_dedupes_and_folds_scores() -> None:
    shared = uuid.uuid4()
    v = Candidate(shared, uuid.uuid4(), "d", 0, "x", vector_score=0.8, sources=["vector"])
    k = Candidate(shared, uuid.uuid4(), "d", 0, "x", keyword_score=0.3, sources=["keyword"])
    other = _candidate("y", source="keyword")

    merged = merge_candidates([v], [k, other])

    assert len(merged) == 2
    folded = next(c for c in merged if c.chunk_id == shared)
    assert folded.vector_score == 0.8
    assert folded.keyword_score == 0.3
    assert set(folded.sources) == {"vector", "keyword"}


async def test_ingest_and_search_carry_the_scope() -> None:
    store = FakeStore(vector_hits=[_candidate("x", source="vector")])
    pipeline = RagPipeline(embedder=FakeEmbedder(), reranker=FakeReranker(), store=store)

    await pipeline.ingest(
        None, scope=SCOPE, filename="a.txt", content_type="text/plain", data=b"hello world"  # type: ignore[arg-type]
    )
    await pipeline.search(None, "x", scope=SCOPE)  # type: ignore[arg-type]

    assert store.documents[0]["scope"] == SCOPE
    assert store.search_scopes == [SCOPE, SCOPE]  # both retrievers


class RecordingSession:
    """Captures the SQL + params VectorStore sends; returns no rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        self.calls.append((str(statement), params or {}))

        class _Result:
            rowcount = 0

            def mappings(self):  # type: ignore[no-untyped-def]
                class _M(list):
                    def all(self):  # type: ignore[no-untyped-def]
                        return list(self)

                return _M()

        return _Result()


async def test_every_store_read_filters_by_user_and_project() -> None:
    store = VectorStore()
    session = RecordingSession()

    await store.list_documents(session, SCOPE)  # type: ignore[arg-type]
    await store.delete_document(session, uuid.uuid4(), SCOPE)  # type: ignore[arg-type]
    await store.vector_search(session, [0.1, 0.2], 5, SCOPE)  # type: ignore[arg-type]
    await store.keyword_search(session, "q", 5, SCOPE)  # type: ignore[arg-type]

    assert len(session.calls) == 4
    for sql, params in session.calls:
        assert "d.user_id = :scope_user AND d.project_id = :scope_project" in sql
        assert params["scope_user"] == SCOPE.user_id
        assert params["scope_project"] == SCOPE.project_id


@pytest.mark.parametrize("bad", [b"", b"   "])
async def test_ingest_rejects_empty_documents(bad: bytes) -> None:
    pipeline = RagPipeline(embedder=FakeEmbedder(), reranker=FakeReranker(), store=FakeStore())
    with pytest.raises(Exception):
        await pipeline.ingest(
            None, scope=SCOPE, filename="empty.txt", content_type="text/plain", data=bad  # type: ignore[arg-type]
        )
