"""
Shared LangGraph graph state.
All nodes read from and write to this TypedDict.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class GraphState(TypedDict):
    # Conversation history (messages accumulate via add_messages reducer)
    messages: Annotated[list[BaseMessage], add_messages]

    # Which specialist agent was last routed to
    active_agent: str | None

    # The supervisor's most recent decision (recomputed every loop pass)
    # {"next": str, "skill": str | None, "input": dict, "reason": str}
    # `next` is a known agent id, or "respond" to compose the final answer.
    route_decision: dict[str, Any] | None

    # How many specialist (delegate) hops have run this turn — the supervisor
    # loop's bound. Seeded to 0 each turn; incremented by `delegate_node`.
    step_count: int

    # Execution mode for the turn: "auto" (supervisor decides), "agent" (prefer
    # specialists), or "chat" (short-circuit straight to respond, no specialists).
    mode: str

    # Retrieved RAG context (populated by RAG node)
    rag_context: list[dict[str, Any]]

    # Structured artifacts produced by agents this turn (code, summaries, images).
    # Per-turn scoped: `delegate_node` accumulates into it (no reducer — it rewrites
    # the full list), and the runtime reseeds it to [] at the start of each turn so
    # the supervisor only sees the current turn's steps.
    artifacts: list[dict[str, Any]]

    # Human-in-the-loop interrupt payload (Phase 7)
    pending_approval: dict[str, Any] | None

    # Run metadata
    run_id: str
    session_id: str
    user_id: str
