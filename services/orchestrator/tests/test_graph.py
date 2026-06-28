"""Phase 1: the single-node graph runs the LLM and accumulates messages."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from orchestrator.graph import build_graph
from orchestrator.state import GraphState


def _initial_state(text: str) -> GraphState:
    return {
        "messages": [HumanMessage(content=text)],
        "active_agent": None,
        "rag_context": [],
        "pending_approval": None,
        "run_id": "r",
        "session_id": "s",
        "user_id": "u",
    }


@pytest.mark.asyncio
async def test_agent_node_appends_assistant_reply() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage(content="Hello there")]))
    graph = build_graph(model, SystemMessage(content="sys"), checkpointer=MemorySaver())

    result = await graph.ainvoke(_initial_state("hi"), {"configurable": {"thread_id": "t"}})

    assert result["active_agent"] == "assistant"
    assert result["messages"][-1].content == "Hello there"


@pytest.mark.asyncio
async def test_checkpointer_accumulates_history_across_turns() -> None:
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content="first"), AIMessage(content="second")])
    )
    graph = build_graph(model, SystemMessage(content="sys"), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "thread-1"}}

    await graph.ainvoke(_initial_state("one"), config)
    # Second turn feeds only the new message; the checkpointer restores the rest.
    result = await graph.ainvoke({"messages": [HumanMessage(content="two")]}, config)

    contents = [m.content for m in result["messages"]]
    assert contents == ["one", "first", "two", "second"]
