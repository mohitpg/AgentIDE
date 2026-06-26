"""
Shared helpers for the supervisor graph's nodes.

Each node lives in its own module as a small callable class; these are the bits
they have in common — the custom-stream writer (a no-op under `ainvoke`) and a
couple of message-history accessors.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from orchestrator.state import GraphState

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
