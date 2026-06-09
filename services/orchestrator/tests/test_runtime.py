"""Phase 1: stream_chat emits the unified event sequence and assembles tokens."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from orchestrator.graph import build_graph
from orchestrator.llm import LLMConfig
from orchestrator.runtime import Orchestrator, _chunk_text
from schemas.events import EventType


def test_chunk_text_handles_string_and_blocks() -> None:
    from langchain_core.messages import AIMessageChunk

    assert _chunk_text(AIMessageChunk(content="hi")) == "hi"
    blocks = [
        {"type": "text", "text": "abc"},
        {"type": "tool_use", "name": "x"},
        {"type": "text", "text": "def"},
    ]
    assert _chunk_text(AIMessageChunk(content=blocks)) == "abcdef"


@pytest.mark.asyncio
async def test_stream_chat_event_sequence() -> None:
    orch = Orchestrator(LLMConfig(api_key=""), "postgresql://unused")
    model = GenericFakeChatModel(messages=iter([AIMessage(content="Hello there")]))
    # Inject a graph directly instead of calling startup() (no DB / API key needed).
    orch._graph = build_graph(model, SystemMessage(content="sys"), checkpointer=MemorySaver())

    events = [
        e
        async for e in orch.stream_chat(
            content="hi", run_id="run-1", session_id="sess-1", user_id="u"
        )
    ]

    assert events[0].type == EventType.AGENT_STEP
    assert events[0].data["phase"] == "enter"
    assert events[-1].type == EventType.DONE

    tokens = [e for e in events if e.type == EventType.TOKEN]
    assert "".join(e.data["content"] for e in tokens) == "Hello there"
    assert all(e.run_id == "run-1" for e in events)


@pytest.mark.asyncio
async def test_stream_chat_before_startup_raises() -> None:
    orch = Orchestrator(LLMConfig(api_key=""), "postgresql://unused")
    with pytest.raises(RuntimeError):
        async for _ in orch.stream_chat(content="hi", run_id="r", session_id="s", user_id="u"):
            pass
