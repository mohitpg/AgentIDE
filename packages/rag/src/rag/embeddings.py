"""
Embeddings via fastembed (ONNX, CPU, offline after the first model download).

`FastEmbedEmbedder` wraps a fastembed `TextEmbedding` model. Models are heavy to
construct (they load weights), so instances are cached per model name and reused
process-wide. All methods are synchronous/CPU-bound — callers should run them in
a thread (`asyncio.to_thread`) from async code, which the pipeline does.
"""

from __future__ import annotations

import threading
from typing import Protocol

# Embeddings are returned as plain Python lists so nothing downstream depends on
# numpy and they serialise straight into SQL parameters.
Vector = list[float]


class Embedder(Protocol):
    """Produces embeddings for documents and queries."""

    @property
    def dim(self) -> int: ...
    def embed_documents(self, texts: list[str]) -> list[Vector]: ...
    def embed_query(self, text: str) -> Vector: ...


class FastEmbedEmbedder:
    """Default embedder backed by a fastembed `TextEmbedding` model."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 384) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self._model_name = model_name
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> Vector:
        # BGE-style models prepend a retrieval instruction to queries; fastembed
        # exposes `query_embed` for that. Fall back to `embed` for models without.
        embed_query = getattr(self._model, "query_embed", None)
        gen = embed_query([text]) if embed_query is not None else self._model.embed([text])
        return next(iter(gen)).tolist()


_lock = threading.Lock()
_cache: dict[str, FastEmbedEmbedder] = {}


def get_embedder(model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 384) -> FastEmbedEmbedder:
    """Return a process-wide cached embedder for `model_name` (thread-safe)."""
    with _lock:
        embedder = _cache.get(model_name)
        if embedder is None:
            embedder = FastEmbedEmbedder(model_name=model_name, dim=dim)
            _cache[model_name] = embedder
        return embedder
