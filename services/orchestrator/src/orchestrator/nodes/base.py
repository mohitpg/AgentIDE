"""
Shared helpers for the supervisor graph's nodes.

Each node lives in its own module as a small callable class; these are the bits
they have in common — the custom-stream writer (a no-op under `ainvoke`) and the
message-history accessors that read the single source of truth, `state["messages"]`
(accumulated across turns by the `add_messages` reducer + the checkpointer).
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

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


def content_text(message: object) -> str:
    """Best-effort string body of a chat message (or anything)."""
    content = getattr(message, "content", message)
    return content if isinstance(content, str) else str(content)


def _last_human_index(messages: list[BaseMessage]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return None


def split_turn(
    state: GraphState,
) -> tuple[list[BaseMessage], str, list[BaseMessage]]:
    """Slice the single source of truth into the three views the loop needs.

    Returns `(prior, current_request, this_turn_steps)`:
    - **prior** — every message before the latest human turn: the conversation so
      far (previous turns). Gives every LLM call cross-turn context.
    - **current_request** — the latest human message's text: this turn's ask.
    - **this_turn_steps** — the messages after it: what specialists have produced
      *this* turn (each tagged with the agent `name`). Empty on the first hop.

    Because `messages` is reducer-accumulated and checkpoint-restored, this one
    split replaces the old per-turn `artifacts` list — no separate channel to keep
    in sync, and prior turns are no longer invisible to the supervisor/delegate.
    """
    messages = state["messages"]
    idx = _last_human_index(messages)
    if idx is None:
        return [], "", list(messages)
    return list(messages[:idx]), content_text(messages[idx]), list(messages[idx + 1:])
