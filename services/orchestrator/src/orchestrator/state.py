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

    # The supervisor's routing decision for the current turn
    # {"agent": str, "skill": str | None, "input": dict, "reason": str}
    route_decision: dict[str, Any] | None

    # Retrieved RAG context (populated by RAG node)
    rag_context: list[dict[str, Any]]

    # Structured artifacts produced by agents (code, summaries, images)
    artifacts: list[dict[str, Any]]

    # Human-in-the-loop interrupt payload (Phase 7)
    pending_approval: dict[str, Any] | None

    # Run metadata
    run_id: str
    session_id: str
    user_id: str
