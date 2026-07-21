"""
High-level RAG pipeline: ingest documents and answer queries.

Ties the pieces together:

  ingest(bytes)  → extract text → chunk → embed → upsert into pgvector
  search(query)  → embed query → hybrid retrieve (vector + full-text)
                 → merge candidates → cross-encoder rerank → top-k

"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from rag.chunking import chunk_text
from rag.config import RagConfig
from rag.embeddings import Embedder, get_embedder
from rag.loaders import extract_text
from rag.reranker import Reranker, apply_rerank, get_reranker
from rag.store import Candidate, ChunkInput, VectorStore, merge_candidates


@dataclass
class IngestResult:
    document_id: uuid.UUID
    filename: str
    num_chunks: int


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    score: float  # cross-encoder relevance
    sources: list[str]  # which first-stage retrievers found it


class RagPipeline:
    def __init__(
        self,
        config: RagConfig | None = None,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        store: VectorStore | None = None,
    ) -> None:
        self.config = config or RagConfig()
        self._embedder = embedder
        self._reranker = reranker
        self.store = store or VectorStore()

    # ── Lazy model resolution (heavy; built on first use) ─────────────────────
    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder(self.config.embedding_model, self.config.embedding_dim)
        return self._embedder

    def _get_reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = get_reranker(self.config.reranker_model)
        return self._reranker

    # ── Ingestion ─────────────────────────────────────────────────────────────
    async def ingest(
        self,
        session: AsyncSession,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        source: str = "upload",
        session_id: uuid.UUID | None = None,
    ) -> IngestResult:
        text_content = extract_text(filename, content_type, data)
        chunks = chunk_text(
            text_content,
            max_tokens=self.config.chunk_tokens,
            overlap=self.config.chunk_overlap,
        )
        if not chunks:
            raise ValueError(f"{filename!r} produced no chunks")

        embedder = self._get_embedder()
        embeddings = await asyncio.to_thread(
            embedder.embed_documents, [c.text for c in chunks]
        )

        document_id = await self.store.add_document(
            session,
            filename=filename,
            content_type=content_type,
            source=source,
            session_id=session_id,
            byte_size=len(data),
        )
        await self.store.add_chunks(
            session,
            document_id,
            [
                ChunkInput(
                    index=c.index,
                    content=c.text,
                    token_count=c.token_count,
                    embedding=emb,
                )
                for c, emb in zip(chunks, embeddings, strict=True)
            ],
        )
        return IngestResult(
            document_id=document_id, filename=filename, num_chunks=len(chunks)
        )

    # ── Retrieval ─────────────────────────────────────────────────────────────
    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
    ) -> list[SearchHit]:
        top_k = top_k or self.config.top_k
        candidate_k = candidate_k or self.config.candidate_k

        embedder = self._get_embedder()
        query_embedding = await asyncio.to_thread(embedder.embed_query, query)

        vector_hits = await self.store.vector_search(session, query_embedding, candidate_k)
        keyword_hits = await self.store.keyword_search(session, query, candidate_k)
        candidates: list[Candidate] = merge_candidates(vector_hits, keyword_hits)
        if not candidates:
            return []

        reranker = self._get_reranker()
        ranked = await asyncio.to_thread(
            apply_rerank, reranker, query, candidates, lambda c: c.content, top_k
        )
        return [
            SearchHit(
                chunk_id=r.item.chunk_id,
                document_id=r.item.document_id,
                filename=r.item.filename,
                chunk_index=r.item.chunk_index,
                content=r.item.content,
                score=r.score,
                sources=r.item.sources,
            )
            for r in ranked
        ]
