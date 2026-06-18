"""Phase 2: the supervisor routes to a specialist or answers inline."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from orchestrator.directory import AgentDirectory
from orchestrator.state import GraphState
from orchestrator.supervisor import _parse_decision, build_supervisor_graph


class FakeA2AClient:
    """Yields one token chunk + a done event, mimicking an agent's SSE stream."""

    def __init__(self, text: str, result: dict | None = None) -> None:
        self.text = text
        self.result = result or {}
        self.calls: list[tuple[str, str, dict]] = []

    async def stream(self, base_url, skill_id, input_data, run_id=None):
        self.calls.append((base_url, skill_id, input_data))
        yield {"type": "token", "run_id": run_id, "data": {"content": self.text, "index": 0}}
        yield {"type": "done", "run_id": run_id, "data": self.result}


def _state(text: str) -> GraphState:
    return {
        "messages": [HumanMessage(content=text)],
        "active_agent": None,
        "route_decision": None,
        "rag_context": [],
        "artifacts": [],
        "pending_approval": None,
        "run_id": "r",
        "session_id": "s",
        "user_id": "u",
    }


def _router(json_decision: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=json_decision)]))


def _general(text: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=text)]))


# ── decision parsing ──────────────────────────────────────────────────────────


def test_parse_decision_valid_code():
    d = _parse_decision('{"agent": "code", "skill": "generate", "reason": "x"}')
    assert d == {"agent": "code", "skill": "generate", "reason": "x"}


def test_parse_decision_snaps_invalid_skill_to_valid_one():
    d = _parse_decision('{"agent": "code", "skill": "bogus"}')
    assert d["agent"] == "code"
    assert d["skill"] in {"generate", "edit", "explain"}


def test_parse_decision_falls_back_to_general_on_garbage():
    assert _parse_decision("not json at all")["agent"] == "general"


def test_parse_decision_snaps_invalid_vision_skill():
    d = _parse_decision('{"agent": "vision", "skill": "bogus"}')
    assert d["agent"] == "vision"
    assert d["skill"] in {"analyze-image", "ocr", "diagram-to-code"}


# ── routing through the graph ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routes_to_code_agent_and_records_artifact():
    client = FakeA2AClient("print('hi')", result={"output": "print('hi')"})
    graph = build_supervisor_graph(
        router_model=_router('{"agent": "code", "skill": "generate", "reason": "write code"}'),
        general_model=_general("unused"),
        general_system=SystemMessage(content="sys"),
        client=client,
        directory=AgentDirectory(),
        checkpointer=MemorySaver(),
    )

    cfg = {"configurable": {"thread_id": "t"}}
    result = await graph.ainvoke(_state("write a hello world"), cfg)

    assert result["active_agent"] == "code"
    assert result["messages"][-1].content == "print('hi')"
    assert result["artifacts"][-1]["agent"] == "code"
    # the code agent was called on its directory URL with the generate skill
    assert client.calls[0][0] == "http://localhost:8102"
    assert client.calls[0][1] == "generate"


@pytest.mark.asyncio
async def test_routes_to_summarizer_with_text_payload():
    client = FakeA2AClient("a short summary", result={"summary": "a short summary"})
    graph = build_supervisor_graph(
        router_model=_router('{"agent": "summarizer", "skill": "tl;dr"}'),
        general_model=_general("unused"),
        general_system=SystemMessage(content="sys"),
        client=client,
        directory=AgentDirectory(),
        checkpointer=MemorySaver(),
    )

    result = await graph.ainvoke(_state("tldr this please"), {"configurable": {"thread_id": "t"}})

    assert result["active_agent"] == "summarizer"
    assert client.calls[0][1] == "tl;dr"
    assert client.calls[0][2] == {"text": "tldr this please"}


@pytest.mark.asyncio
async def test_routes_to_vision_agent_for_image():
    client = FakeA2AClient("a red square", result={"output": "a red square"})
    graph = build_supervisor_graph(
        router_model=_router('{"agent": "vision", "skill": "analyze-image", "reason": "image"}'),
        general_model=_general("unused"),
        general_system=SystemMessage(content="sys"),
        client=client,
        directory=AgentDirectory(),
        checkpointer=MemorySaver(),
    )

    result = await graph.ainvoke(
        _state("what is this? data:image/png;base64,AAAA"), {"configurable": {"thread_id": "t"}}
    )

    assert result["active_agent"] == "vision"
    # the vision agent was called on its directory URL with the analyze-image skill
    assert client.calls[0][0] == "http://localhost:8104"
    assert client.calls[0][1] == "analyze-image"


@pytest.mark.asyncio
async def test_general_fallback_answers_inline():
    client = FakeA2AClient("never called")
    graph = build_supervisor_graph(
        router_model=_router('{"agent": "general", "skill": null}'),
        general_model=_general("Hi, how can I help?"),
        general_system=SystemMessage(content="sys"),
        client=client,
        directory=AgentDirectory(),
        checkpointer=MemorySaver(),
    )

    result = await graph.ainvoke(_state("hello"), {"configurable": {"thread_id": "t"}})

    assert result["active_agent"] == "assistant"
    assert result["messages"][-1].content == "Hi, how can I help?"
    assert client.calls == []  # specialist never invoked


@pytest.mark.asyncio
async def test_delegate_degrades_when_agent_unavailable():
    class BrokenClient:
        async def stream(self, *a, **k):
            raise ConnectionError("connection refused")
            yield  # pragma: no cover - makes this an async generator

    graph = build_supervisor_graph(
        router_model=_router('{"agent": "code", "skill": "generate"}'),
        general_model=_general("unused"),
        general_system=SystemMessage(content="sys"),
        client=BrokenClient(),
        directory=AgentDirectory(),
        checkpointer=MemorySaver(),
    )

    result = await graph.ainvoke(_state("write code"), {"configurable": {"thread_id": "t"}})

    assert "unavailable" in result["messages"][-1].content
