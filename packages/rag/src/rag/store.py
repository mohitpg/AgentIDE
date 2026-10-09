"""
pgvector storage + hybrid retrieval.

`packages/rag` stays decoupled from `apps/api`: the store never imports ORM
models or app settings — it runs parameterised SQL against an injected
`AsyncSession`, and the table schema is owned by an Alembic migration in the API
(`0005_create_rag_tables`). Two tables back it:

  rag_documents  — one row per uploaded document (metadata + chunk count)
  rag_chunks     — one row per chunk: content, `embedding vector(384)` (HNSW,
                   cosine), and a generated `tsvector` (GIN) for full-text.

Retrieval runs two first-stage searches independently:
  * vector   — approximate-nearest-neighbour over the HNSW index (semantic)
  * keyword  — Postgres full-text `ts_rank_cd` over the tsvector (BM25-style)

`merge_candidates` unions them into one candidate pool (a chunk found by both
keeps both rank signals) which the pipeline then reranks.

Row scoping: every document belongs to a user + project (`RagScope`), and every
read, search, and delete filters on both. There is deliberately no unscoped
variant, so a caller can't forget the filter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.embeddings import Vector


def _vec_literal(vector: Vector) -> str:
    """Format a vector as the pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


@dataclass(frozen=True)
class RagScope:
    """The owner of a document set: one user's one project."""

    user_id: uuid.UUID
    project_id: uuid.UUID


# Appended to every query that touches rag_documents `d`.
_SCOPE_SQL = "d.user_id = :scope_user AND d.project_id = :scope_project"


def _scope_params(scope: RagScope) -> dict:
    return {"scope_user": scope.user_id, "scope_project": scope.project_id}


@dataclass
class Candidate:
    """A retrieved chunk plus where it came from in the first stage."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    vector_score: float | None = None  # cosine similarity (1 - distance)
    keyword_score: float | None = None  # ts_rank_cd
    sources: list[str] = field(default_factory=list)  # "vector" / "keyword"


def _row_to_candidate(row: dict, source: str, score: float) -> Candidate:
    cand = Candidate(
        chunk_id=row["id"],
        document_id=row["document_id"],
        filename=row["filename"],
        chunk_index=row["chunk_index"],
        content=row["content"],
        sources=[source],
    )
    if source == "vector":
        cand.vector_score = score
    else:
        cand.keyword_score = score
    return cand


def merge_candidates(
    vector_hits: list[Candidate], keyword_hits: list[Candidate]
) -> list[Candidate]:
    """Union the two hit lists by chunk id, folding scores from both retrievers."""
    merged: dict[uuid.UUID, Candidate] = {}
    for hit in (*vector_hits, *keyword_hits):
        existing = merged.get(hit.chunk_id)
        if existing is None:
            merged[hit.chunk_id] = hit
            continue
        # Same chunk found by both retrievers — combine the signals.
        if hit.vector_score is not None:
            existing.vector_score = hit.vector_score
        if hit.keyword_score is not None:
            existing.keyword_score = hit.keyword_score
        for src in hit.sources:
            if src not in existing.sources:
                existing.sources.append(src)
    return list(merged.values())


class VectorStore:
    """Async pgvector data-access for documents and chunks."""

    async def add_document(
        self,
        session: AsyncSession,
        *,
        scope: RagScope,
        filename: str,
        content_type: str | None,
        source: str,
        session_id: uuid.UUID | None,
        byte_size: int,
        meta: dict | None = None,
    ) -> uuid.UUID:
        doc_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO rag_documents
                    (id, user_id, project_id, filename, content_type, source,
                     session_id, byte_size, meta)
                VALUES
                    (:id, :scope_user, :scope_project, :filename, :content_type,
                     :source, :session_id, :byte_size, CAST(:meta AS jsonb))
                """
            ),
            {
                "id": doc_id,
                **_scope_params(scope),
                "filename": filename,
                "content_type": content_type,
                "source": source,
                "session_id": session_id,
                "byte_size": byte_size,
                "meta": _json(meta),
            },
        )
        return doc_id

    async def add_chunks(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
        chunks: list[ChunkInput],
    ) -> None:
        if not chunks:
            return
        await session.execute(
            text(
                """
                INSERT INTO rag_chunks
                    (id, document_id, chunk_index, content, token_count, embedding)
                VALUES
                    (:id, :document_id, :chunk_index, :content, :token_count,
                     CAST(:embedding AS vector))
                """
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "chunk_index": c.index,
                    "content": c.content,
                    "token_count": c.token_count,
                    "embedding": _vec_literal(c.embedding),
                }
                for c in chunks
            ],
        )
        await session.execute(
            text("UPDATE rag_documents SET num_chunks = :n WHERE id = :id"),
            {"n": len(chunks), "id": document_id},
        )

    async def list_documents(self, session: AsyncSession, scope: RagScope) -> list[dict]:
        result = await session.execute(
            text(
                f"""
                SELECT id, filename, content_type, source, session_id,
                       num_chunks, byte_size, created_at
                FROM rag_documents d
                WHERE {_SCOPE_SQL}
                ORDER BY created_at DESC
                """
            ),
            _scope_params(scope),
        )
        return [dict(r) for r in result.mappings().all()]

    async def delete_document(
        self, session: AsyncSession, document_id: uuid.UUID, scope: RagScope
    ) -> bool:
        result = await session.execute(
            text(f"DELETE FROM rag_documents d WHERE d.id = :id AND {_SCOPE_SQL}"),
            {"id": document_id, **_scope_params(scope)},
        )
        return result.rowcount > 0

    async def vector_search(
        self, session: AsyncSession, query_embedding: Vector, limit: int, scope: RagScope
    ) -> list[Candidate]:
        # The scope filter runs after the HNSW scan, so a project holding a small
        # share of all chunks can get fewer than `limit` candidates back.
        result = await session.execute(
            text(
                f"""
                SELECT c.id, c.document_id, c.chunk_index, c.content, d.filename,
                       1 - (c.embedding <=> CAST(:qv AS vector)) AS score
                FROM rag_chunks c
                JOIN rag_documents d ON d.id = c.document_id
                WHERE {_SCOPE_SQL}
                ORDER BY c.embedding <=> CAST(:qv AS vector)
                LIMIT :limit
                """
            ),
            {"qv": _vec_literal(query_embedding), "limit": limit, **_scope_params(scope)},
        )
        return [_row_to_candidate(dict(r), "vector", r["score"]) for r in result.mappings()]

    async def keyword_search(
        self, session: AsyncSession, query: str, limit: int, scope: RagScope
    ) -> list[Candidate]:
        result = await session.execute(
            text(
                f"""
                SELECT c.id, c.document_id, c.chunk_index, c.content, d.filename,
                       ts_rank_cd(c.tsv, plainto_tsquery('english', :q)) AS score
                FROM rag_chunks c
                JOIN rag_documents d ON d.id = c.document_id
                WHERE c.tsv @@ plainto_tsquery('english', :q) AND {_SCOPE_SQL}
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"q": query, "limit": limit, **_scope_params(scope)},
        )
        return [_row_to_candidate(dict(r), "keyword", r["score"]) for r in result.mappings()]


@dataclass(frozen=True)
class ChunkInput:
    """A chunk ready to persist (text + its embedding)."""

    index: int
    content: str
    token_count: int
    embedding: Vector


def _json(meta: dict | None) -> str:
    import json

    return json.dumps(meta or {})
