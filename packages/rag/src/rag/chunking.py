"""
Token-aware chunking with overlap.

Tokenisation is pluggable behind the `Tokenizer` protocol:
  * `TiktokenTokenizer` (default) — true BPE tokens via `tiktoken` (cl100k_base).
  * `WordTokenizer` — whitespace fallback, used when tiktoken can't load (e.g.
    offline) and in unit tests so chunking is exercisable with no model/network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    """Encode text to integer tokens and back."""

    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...


class WordTokenizer:
    """Whitespace tokenizer — each whitespace-delimited word is one 'token'.

    Tokens are indices into a per-call vocabulary, so `decode(encode(x))` round
    trips (collapsing runs of whitespace to single spaces, which is fine for
    chunking). Dependency-free and deterministic.
    """

    def encode(self, text: str) -> list[int]:
        self._vocab = text.split()
        return list(range(len(self._vocab)))

    def decode(self, tokens: list[int]) -> str:
        return " ".join(self._vocab[t] for t in tokens)


class TiktokenTokenizer:
    """BPE tokenizer backed by tiktoken's `cl100k_base` encoding."""

    def __init__(self) -> None:
        import tiktoken

        self._enc = tiktoken.get_encoding("cl100k_base")

    def encode(self, text: str) -> list[int]:
        return self._enc.encode(text)

    def decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)


def _default_tokenizer() -> Tokenizer:
    """tiktoken if it loads, else the whitespace fallback (offline-safe)."""
    try:
        return TiktokenTokenizer()
    except Exception:  # tiktoken missing or BPE file unavailable offline
        return WordTokenizer()


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int


def chunk_text(
    text: str,
    *,
    max_tokens: int = 512,
    overlap: int = 64,
    tokenizer: Tokenizer | None = None,
) -> list[Chunk]:
    """
    Split `text` into overlapping token windows.

    `overlap` tokens are repeated between consecutive chunks. Returns chunks in
    document order; an empty / whitespace-only input yields no chunks.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not 0 <= overlap < max_tokens:
        raise ValueError("overlap must satisfy 0 <= overlap < max_tokens")

    tok = tokenizer or _default_tokenizer()
    # WordTokenizer keeps per-call state in encode(); decode must use the same
    # instance, so encode once up front and reuse `tok` for every decode below.
    token_ids = tok.encode(text)
    if not token_ids:
        return []

    stride = max_tokens - overlap
    chunks: list[Chunk] = []
    for start in range(0, len(token_ids), stride):
        window = token_ids[start : start + max_tokens]
        if not window:
            break
        chunk_str = tok.decode(window).strip()
        if chunk_str:
            chunks.append(Chunk(index=len(chunks), text=chunk_str, token_count=len(window)))
        if start + max_tokens >= len(token_ids):
            break
    return chunks
