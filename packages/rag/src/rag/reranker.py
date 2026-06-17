"""
Cross-encoder reranking via sentence-transformers.

Hybrid retrieval (vector + full-text) is recall-oriented and produces a noisy
candidate pool. A cross-encoder jointly encodes (query, chunk) and scores their
relevance directly, which is far more precise than the first-stage similarity —
so we use it to reorder candidates and keep the best top-k.

Like the embedder, the model is heavy and CPU-bound, so instances are cached per
model name and reused; call `rerank` inside a thread from async code.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one relevance score per document, aligned with `documents`."""
        ...


class CrossEncoderReranker:
    """Default reranker backed by a sentence-transformers `CrossEncoder`."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)
        self._model_name = model_name

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        pairs = [(query, doc) for doc in documents]
        scores = self._model.predict(pairs)
        return [float(s) for s in scores]


_lock = threading.Lock()
_cache: dict[str, CrossEncoderReranker] = {}


def get_reranker(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> CrossEncoderReranker:
    """Return a process-wide cached reranker for `model_name` (thread-safe)."""
    with _lock:
        reranker = _cache.get(model_name)
        if reranker is None:
            reranker = CrossEncoderReranker(model_name=model_name)
            _cache[model_name] = reranker
        return reranker


@dataclass(frozen=True)
class Ranked[T]:
    item: T
    score: float


def apply_rerank[T](
    reranker: Reranker,
    query: str,
    items: list[T],
    get_text: Callable[[T], str],
    top_k: int,
) -> list[Ranked[T]]:
    """Score `items` with the reranker, sort by score desc, return the top_k."""
    if not items:
        return []
    scores = reranker.rerank(query, [get_text(it) for it in items])
    ranked = [Ranked(item=it, score=s) for it, s in zip(items, scores, strict=True)]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:top_k]
