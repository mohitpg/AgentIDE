"""Phase 1 (new flow): the supervisor runs as a recurrent loop — it picks the
next specialist, sees its output, then loops or composes the final answer."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from orchestrator.directory import AgentDirectory
from orchestrator.state import GraphState
from orchestrator.supervisor import STEP_CAP, _parse_decision, build_supervisor_graph


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


class MultiA2AClient:
    """Returns a canned token stream keyed by skill, recording every call in order."""

    def __init__(self, by_skill: dict[str, str]) -> None:
        self.by_skill = by_skill
        self.calls: list[tuple[str, str, dict]] = []

    async def stream(self, base_url, skill_id, input_data, run_id=None):
        self.calls.append((base_url, skill_id, input_data))
        text = self.by_skill.get(skill_id, "")
        yield {"type": "token", "run_id": run_id, "data": {"content": text, "index": 0}}
        yield {"type": "done", "run_id": run_id, "data": {"output": text}}


def _state(text: str, *, mode: str = "auto", step_count: int = 0) -> GraphState:
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
        "step_count": step_count,
        "mode": mode,
    }


def _router(*json_decisions: str) -> GenericFakeChatModel:
    """A supervisor model that returns the given decisions across successive passes."""
    return GenericFakeChatModel(
        messages=iter([AIMessage(content=d) for d in json_decisions])
    )


class _RecordingRouter:
    """Router that records the prompt text it saw, then returns canned decisions."""

    def __init__(self, *json_decisions: str) -> None:
        self._decisions = iter(json_decisions)
        self.seen: list[str] = []

    async def ainvoke(self, messages, *a, **k):
        self.seen.append("\n".join(str(m.content) for m in messages))
        return AIMessage(content=next(self._decisions))


def _general(text: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=text)]))


def _build(router, general, client, *, directory=None):
    return build_supervisor_graph(
        router_model=router,
        general_model=general,
        general_system=SystemMessage(content="sys"),
        client=client,
        directory=directory or AgentDirectory(),
        checkpointer=MemorySaver(),
    )


_CFG = {"configurable": {"thread_id": "t"}}

# A stored-image reference token, as minted by the gateway's POST /images.
_REF = "<Image at db.images.123e4567-e89b-12d3-a456-426614174000>"


# ── decision parsing ──────────────────────────────────────────────────────────


def test_parse_decision_valid_code():
    d = _parse_decision('{"next": "code", "skill": "generate", "reason": "x"}')
    assert d == {"next": "code", "skill": "generate", "reason": "x"}


def test_parse_decision_snaps_invalid_skill_to_valid_one():
    d = _parse_decision('{"next": "code", "skill": "bogus"}')
    assert d["next"] == "code"
    assert d["skill"] in {"generate", "edit", "explain"}


def test_parse_decision_falls_back_to_respond_on_garbage():
    assert _parse_decision("not json at all")["next"] == "respond"


def test_parse_decision_snaps_invalid_vision_skill():
    d = _parse_decision('{"next": "vision", "skill": "bogus"}')
    assert d["next"] == "vision"
    assert d["skill"] in {"analyze-image", "ocr", "diagram-to-code"}


def test_parse_decision_respond_sentinel():
    d = _parse_decision('{"next": "respond", "reason": "all done"}')
    assert d == {"next": "respond", "skill": None, "reason": "all done"}


def test_parse_decision_unknown_next_snaps_to_respond():
    assert _parse_decision('{"next": "wizard", "skill": "x"}')["next"] == "respond"


def test_parse_decision_accepts_legacy_agent_key():
    # The model may still emit the old {"agent": ...} shape.
    assert _parse_decision('{"agent": "code", "skill": "generate"}')["next"] == "code"


# ── the loop ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trivial_turn_responds_without_delegating():
    """Fast path: a conversational turn goes straight to respond, no specialist."""
    client = FakeA2AClient("never called")
    graph = _build(_router('{"next": "respond"}'), _general("Hi, how can I help?"), client)

    result = await graph.ainvoke(_state("hi"), _CFG)

    assert client.calls == []  # no specialist invoked
    assert result["step_count"] == 0
    assert result["active_agent"] == "assistant"
    assert result["messages"][-1].content == "Hi, how can I help?"


@pytest.mark.asyncio
async def test_vision_then_code_then_respond_sequence():
    """A multi-agent task: vision reads the image, then code acts, then respond."""
    client = MultiA2AClient(
        {"analyze-image": "an image of a function", "edit": "def f():\n    return 1"}
    )
    graph = _build(
        _router(
            '{"next": "vision", "skill": "analyze-image", "reason": "read the image first"}',
            '{"next": "code", "skill": "edit", "reason": "now refactor it"}',
            '{"next": "respond", "reason": "done"}',
        ),
        _general("Here is the refactored function."),
        client,
    )

    result = await graph.ainvoke(_state("refactor this image of code"), _CFG)

    # both specialists ran, in order, each on its own directory URL
    assert [c[1] for c in client.calls] == ["analyze-image", "edit"]
    assert client.calls[0][0] == "http://localhost:8104"  # vision
    assert client.calls[1][0] == "http://localhost:8102"  # code
    # both outputs recorded as artifacts, in order
    assert [a["agent"] for a in result["artifacts"]] == ["vision", "code"]
    assert result["step_count"] == 2  # two delegate hops
    # respond composed the single final answer
    assert result["active_agent"] == "assistant"
    assert result["messages"][-1].content == "Here is the refactored function."


@pytest.mark.asyncio
async def test_downstream_agent_receives_upstream_output():
    """The code agent must see what vision extracted, not the original image blob.

    Regression: `_build_skill_input` used to forward only the original user message
    to every hop, so a downstream agent was blind to upstream artifacts.
    """
    client = MultiA2AClient(
        {"analyze-image": "def f():\n    return 1", "edit": "def f():\n    return 2"}
    )
    graph = _build(
        _router(
            '{"next": "vision", "skill": "analyze-image"}',
            '{"next": "code", "skill": "edit"}',
            '{"next": "respond"}',
        ),
        _general("done"),
        client,
    )

    await graph.ainvoke(_state(f"refactor this {_REF}"), _CFG)

    vision_payload = client.calls[0][2]["text"]
    code_payload = client.calls[1][2]["text"]
    # vision (first hop) gets the image reference verbatim...
    assert _REF in vision_payload
    # ...and the code agent sees vision's extracted code (the upstream artifact).
    assert "def f():\n    return 1" in code_payload


@pytest.mark.asyncio
async def test_step_cap_forces_respond():
    """The loop is bounded: at the cap the supervisor responds without delegating."""
    client = FakeA2AClient("should not run")
    # The model would keep delegating, but the cap short-circuits before any LLM call.
    graph = _build(_router('{"next": "code", "skill": "generate"}'), _general("final"), client)

    result = await graph.ainvoke(_state("keep going", step_count=STEP_CAP), _CFG)

    assert client.calls == []  # cap routed straight to respond
    assert result["active_agent"] == "assistant"
    assert result["messages"][-1].content == "final"


@pytest.mark.asyncio
async def test_chat_mode_short_circuits_to_respond():
    """`mode="chat"` never delegates, even when the request looks specialist-shaped."""
    client = FakeA2AClient("should not run")
    # No decisions queued: chat mode must not call the supervisor model at all.
    graph = _build(_router(), _general("plain answer"), client)

    result = await graph.ainvoke(_state("write me some code", mode="chat"), _CFG)

    assert client.calls == []
    assert result["messages"][-1].content == "plain answer"


@pytest.mark.asyncio
async def test_delegate_degrades_when_agent_unavailable_and_still_finishes():
    """Best-effort: a down agent yields an error note, and the turn still responds."""

    class BrokenClient:
        async def stream(self, *a, **k):
            raise ConnectionError("connection refused")
            yield  # pragma: no cover - makes this an async generator

    graph = _build(
        _router('{"next": "code", "skill": "generate"}', '{"next": "respond"}'),
        _general("Sorry — the code agent was unavailable, try again shortly."),
        BrokenClient(),
    )

    result = await graph.ainvoke(_state("write code"), _CFG)

    # the failure was recorded as an artifact the supervisor could see...
    assert any("unavailable" in a["result"].get("text", "") for a in result["artifacts"])
    assert result["step_count"] == 1  # the failed hop still counts (no infinite loop)
    # ...and the run still produced a final answer
    assert result["active_agent"] == "assistant"
    assert "unavailable" in result["messages"][-1].content


# ── stored-image references ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_ref_forwarded_to_vision():
    """The image reference is compact, so it passes through to the router as-is
    and reaches the vision agent verbatim in its A2A payload (no redaction)."""
    router = _RecordingRouter(
        '{"next": "vision", "skill": "analyze-image"}',
        '{"next": "respond"}',
    )
    client = MultiA2AClient({"analyze-image": "a screenshot of code"})
    graph = _build(router, _general("Here's what the image shows."), client)

    await graph.ainvoke(_state(f"explain this {_REF}"), _CFG)

    # The router sees the reference (its signal to route to vision)...
    assert _REF in router.seen[0]
    # ...and the delegate forwards it to the vision agent, which resolves it.
    assert _REF in client.calls[0][2]["text"]


@pytest.mark.asyncio
async def test_summarizer_receives_text_payload():
    """The chosen skill + raw turn text reach the specialist over A2A."""
    client = FakeA2AClient("a short summary", result={"summary": "a short summary"})
    graph = _build(
        _router('{"next": "summarizer", "skill": "tl;dr"}', '{"next": "respond"}'),
        _general("Summary: a short summary"),
        client,
    )

    result = await graph.ainvoke(_state("tldr this please"), _CFG)

    assert client.calls[0][1] == "tl;dr"
    assert client.calls[0][2] == {"text": "tldr this please"}
    assert result["artifacts"][-1]["agent"] == "summarizer"
    assert result["active_agent"] == "assistant"
