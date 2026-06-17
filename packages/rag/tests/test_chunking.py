"""Chunking tests — run with the dependency-free WordTokenizer (no network)."""

from __future__ import annotations

from rag.chunking import WordTokenizer, chunk_text


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def test_empty_input_yields_no_chunks() -> None:
    assert chunk_text("", tokenizer=WordTokenizer()) == []
    assert chunk_text("   \n  ", tokenizer=WordTokenizer()) == []


def test_single_chunk_when_under_limit() -> None:
    chunks = chunk_text(_words(10), max_tokens=512, overlap=64, tokenizer=WordTokenizer())
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].token_count == 10


def test_windows_overlap_and_are_ordered() -> None:
    chunks = chunk_text(_words(25), max_tokens=10, overlap=4, tokenizer=WordTokenizer())
    # stride = 6 → windows start at 0, 6, 12, 18; the 18-window reaches the last
    # token (24), so iteration stops there — no redundant tail-only window.
    assert [c.index for c in chunks] == [0, 1, 2, 3]

    first = chunks[0].text.split()
    second = chunks[1].text.split()
    assert first == [f"w{i}" for i in range(10)]
    # last 4 tokens of chunk 0 reappear as the first 4 of chunk 1 (overlap)
    assert first[-4:] == second[:4]
    # the final window covers through the last token (no data dropped at the end)
    assert chunks[-1].text.split()[-1] == "w24"


def test_invalid_params_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_text("x", max_tokens=0, tokenizer=WordTokenizer())
    with pytest.raises(ValueError):
        chunk_text("x", max_tokens=10, overlap=10, tokenizer=WordTokenizer())
