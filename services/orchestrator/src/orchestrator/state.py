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
    
    messages: Annotated[list[BaseMessage], add_messages]


    active_agent: str | None


    route_decision: dict[str, Any] | None


    step_count: int


    mode: str

    # Retrieved RAG context (populated by RAG node)
    rag_context: list[dict[str, Any]]

    # Human-in-the-loop interrupt payload (tbd)
    pending_approval: dict[str, Any] | None

    # Run metadata. user_id + project_id are the caller's row scope; the delegate
    # forwards them to agents (A2A metadata) so their data access is scoped too.
    run_id: str
    session_id: str
    user_id: str
    project_id: str
