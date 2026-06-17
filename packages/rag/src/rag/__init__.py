"""
agentide-rag: RAG pipeline utilities.

Public surface:
  RagConfig                     — chunking / embedding / retrieval knobs
  RagPipeline                   — ingest(document) + search(query) → top-k
  IngestResult, SearchHit       — pipeline return types
  chunk_text, Chunk             — token-aware chunking
  extract_text                  — bytes → text (txt/md/pdf/…)
  get_embedder / get_reranker   — cached model factories
  VectorStore                   — pgvector data-access (inject an AsyncSession)

Implemented progressively across phases:
  Phase 4 – chunking, embeddings, pgvector upsert, hybrid search, reranking
  Phase 7 – semantic cache, embedding cache
"""

from __future__ import annotations

from rag.chunking import Chunk, chunk_text
from rag.config import RagConfig
from rag.embeddings import Embedder, get_embedder
from rag.loaders import UnsupportedDocumentError, extract_text
from rag.pipeline import IngestResult, RagPipeline, SearchHit
from rag.reranker import Reranker, get_reranker
from rag.store import Candidate, ChunkInput, VectorStore, merge_candidates

__all__ = [
    "Candidate",
    "Chunk",
    "ChunkInput",
    "Embedder",
    "IngestResult",
    "RagConfig",
    "RagPipeline",
    "Reranker",
    "SearchHit",
    "UnsupportedDocumentError",
    "VectorStore",
    "chunk_text",
    "extract_text",
    "get_embedder",
    "get_reranker",
    "merge_candidates",
]
