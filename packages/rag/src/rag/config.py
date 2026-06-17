"""
RAG configuration.

A small, dependency-light config object (mirrors the `LLMConfig` pattern used by
the orchestrator and agents) so `packages/rag` never imports `api.*`. The API
layer builds a `RagConfig` from its own `Settings` and injects it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RagConfig:
    """Knobs for chunking, embedding, retrieval and reranking."""

    # ── Embeddings (fastembed) ────────────────────────────────────────────────
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384  # MUST match the vector(N) column in the migration

    # ── Reranker (sentence-transformers CrossEncoder) ─────────────────────────
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── Chunking ──────────────────────────────────────────────────────────────
    chunk_tokens: int = 512
    chunk_overlap: int = 64

    # ── Retrieval ─────────────────────────────────────────────────────────────
    # Candidates pulled from EACH retriever (vector + full-text) before reranking.
    candidate_k: int = 20
    # Final number of chunks returned after reranking.
    top_k: int = 5
