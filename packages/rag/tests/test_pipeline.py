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
from rag.store import Candidate, ChunkInput, merge_candidates


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

    async def add_document(self, session, **kwargs) -> uuid.UUID:  # type: ignore[no-untyped-def]
        doc_id = uuid.uuid4()
        self.documents.append({"id": doc_id, **kwargs})
        return doc_id

    async def add_chunks(self, session, document_id, chunks) -> None:  # type: ignore[no-untyped-def]
        self.chunks.extend(chunks)

    async def vector_search(self, session, query_embedding, limit):  # type: ignore[no-untyped-def]
        return self._vector_hits[:limit]

    async def keyword_search(self, session, query, limit):  # type: ignore[no-untyped-def]
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
        None, filename="notes.txt", content_type="text/plain", data=data  # type: ignore[arg-type]
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

    hits = await pipeline.search(None, "quick brown fox")  # type: ignore[arg-type]

    assert len(hits) == 2  # top_k honoured
    # the chunk containing all query terms reranks first
    assert hits[0].content == "the quick brown fox"
    assert hits[0].score >= hits[1].score


async def test_search_empty_corpus_returns_nothing() -> None:
    pipeline = RagPipeline(embedder=FakeEmbedder(), reranker=FakeReranker(), store=FakeStore())
    assert await pipeline.search(None, "anything") == []  # type: ignore[arg-type]


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


@pytest.mark.parametrize("bad", [b"", b"   "])
async def test_ingest_rejects_empty_documents(bad: bytes) -> None:
    pipeline = RagPipeline(embedder=FakeEmbedder(), reranker=FakeReranker(), store=FakeStore())
    with pytest.raises(Exception):
        await pipeline.ingest(
            None, filename="empty.txt", content_type="text/plain", data=bad  # type: ignore[arg-type]
        )
