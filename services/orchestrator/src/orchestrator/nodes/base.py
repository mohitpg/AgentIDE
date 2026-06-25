"""
Shared helpers for the supervisor graph's nodes.

Each node lives in its own module as a small callable class; these are the bits
they have in common — the custom-stream writer (a no-op under `ainvoke`) and a
couple of message-history accessors.
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage

from orchestrator.state import GraphState

# Inline base64 image data URI (kept in sync with the vision agent's extractor).
_DATA_URI_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def redact_images(text: str) -> str:
    """Replace inline base64 image data URIs with a short placeholder.

    Images ride inside the turn text as data URIs (see the vision agent). The full
    blob must reach the vision agent verbatim, but LLM *reasoning* calls — supervisor
    routing and respond synthesis — only need to know an image is present. A pasted
    screenshot is 100KB+ of base64 (~150k tokens); leaving it inline bloats every
    routing pass and can overflow the model's context window, so we strip it here
    while the delegate path forwards the original text untouched.
    """
    return _DATA_URI_RE.sub("[image attached]", text)

try:  # available in langgraph >= 0.2
    from langgraph.config import get_stream_writer
except ImportError:  # pragma: no cover - very old langgraph
    def get_stream_writer():  # type: ignore[misc]
        return lambda _event: None


def stream_writer():
    """Stream writer that degrades to a no-op under ainvoke / non-custom streaming."""
    try:
        return get_stream_writer()
    except Exception:
        return lambda _event: None


def last_human_text(state: GraphState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def history(state: GraphState) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in state["messages"]:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        if isinstance(msg.content, str):
            out.append({"role": role, "content": msg.content})
    return out
