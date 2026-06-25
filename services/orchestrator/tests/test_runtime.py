"""Phase 1 (new flow): the runtime forwards the supervisor loop's StreamEvents,
seeds `step_count`/`mode`, and refreshes the agent directory before each turn."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from orchestrator.directory import AgentDirectory
from orchestrator.llm import LLMConfig
from orchestrator.runtime import Orchestrator
from orchestrator.supervisor import build_supervisor_graph
from schemas.events import EventType


class _FakeA2AClient:
    """Yields one token + a done, mimicking an agent's A2A stream."""

    async def stream(self, base_url, skill_id, input_data, run_id=None):
        yield {"type": "token", "data": {"content": "from agent"}}
        yield {"type": "done", "data": {"output": "from agent"}}


def _supervisor(decisions: list[str], general_text: str, directory: AgentDirectory):
    return build_supervisor_graph(
        router_model=GenericFakeChatModel(
            messages=iter([AIMessage(content=d) for d in decisions])
        ),
        general_model=GenericFakeChatModel(messages=iter([AIMessage(content=general_text)])),
        general_system=SystemMessage(content="sys"),
        client=_FakeA2AClient(),
        directory=directory,
        checkpointer=MemorySaver(),
    )


def _orch(
    directory: AgentDirectory, decisions: list[str], general_text: str, **kwargs
) -> Orchestrator:
    orch = Orchestrator(LLMConfig(api_key=""), "postgresql://unused", **kwargs)
    # Share the same directory object the graph closes over, then inject the graph
    # directly instead of calling startup() (no DB / API key / agent stack needed).
    orch._directory = directory
    orch._graph = _supervisor(decisions, general_text, directory)
    return orch


async def _run(orch: Orchestrator, content: str, run_id: str = "run-1", **kwargs):
    return [
        e
        async for e in orch.stream_chat(
            content=content, run_id=run_id, session_id="s", user_id="u", **kwargs
        )
    ]


@pytest.mark.asyncio
async def test_fast_path_emits_token_and_done_sequence() -> None:
    orch = _orch(AgentDirectory(urls={}), ['{"next": "respond"}'], "Hello there")

    events = await _run(orch, "hi")

    assert events[0].type == EventType.AGENT_STEP
    assert events[0].data["phase"] == "enter"
    assert events[-1].type == EventType.DONE
    assert "duration_ms" in events[-1].data  # runtime stamps the terminal done

    tokens = [e for e in events if e.type == EventType.TOKEN]
    assert "".join(e.data["content"] for e in tokens) == "Hello there"
    # the fast path streams only the final channel (no Thinking block)
    assert all(e.data.get("channel") == "final" for e in tokens)
    assert all(e.run_id == "run-1" for e in events)
    # the fast path emits exactly one DONE (no per-delegate done events)
    assert sum(1 for e in events if e.type == EventType.DONE) == 1


@pytest.mark.asyncio
async def test_delegate_then_respond_streams_both_and_one_done() -> None:
    directory = AgentDirectory(urls={"code": "http://localhost:8102"})
    orch = _orch(
        directory,
        ['{"next": "code", "skill": "generate"}', '{"next": "respond"}'],
        "composed final answer",
    )

    events = await _run(orch, "write code", run_id="r2")

    # the specialist streams on the "thinking" channel, the composed answer on "final"
    thinking = [
        e for e in events if e.type == EventType.TOKEN and e.data.get("channel") == "thinking"
    ]
    final = [
        e for e in events if e.type == EventType.TOKEN and e.data.get("channel") == "final"
    ]
    assert "".join(e.data["content"] for e in thinking) == "from agent"
    assert "".join(e.data["content"] for e in final) == "composed final answer"
    # the trace names the code agent that ran
    assert any(
        e.data.get("agent") == "code" for e in events if e.type == EventType.AGENT_STEP
    )
    # only the terminal respond emits DONE — delegate no longer does
    assert sum(1 for e in events if e.type == EventType.DONE) == 1
    assert events[-1].type == EventType.DONE


@pytest.mark.asyncio
async def test_chat_mode_skips_specialists() -> None:
    directory = AgentDirectory(urls={"code": "http://localhost:8102"})
    # No decisions queued — chat mode must never call the supervisor model.
    orch = _orch(directory, [], "just chatting")

    events = await _run(orch, "write code", run_id="r-chat", mode="chat")

    body = "".join(e.data["content"] for e in events if e.type == EventType.TOKEN)
    assert body == "just chatting"
    # no delegate step ran
    assert not any(e.data.get("node") == "delegate" for e in events)


@pytest.mark.asyncio
async def test_directory_provider_refreshes_before_each_run() -> None:
    # Directory starts empty; without a refresh the supervisor would respond inline.
    directory = AgentDirectory(urls={})

    async def provider() -> dict[str, str]:
        return {"code": "http://localhost:8102"}

    orch = _orch(
        directory,
        ['{"next": "code", "skill": "generate"}', '{"next": "respond"}'],
        "done",
        directory_provider=provider,
    )

    events = await _run(orch, "write code", run_id="r3")

    # The provider populated the directory, so the run reached the code agent.
    assert directory.url_for("code") == "http://localhost:8102"
    body = "".join(e.data["content"] for e in events if e.type == EventType.TOKEN)
    assert "from agent" in body


@pytest.mark.asyncio
async def test_stream_chat_before_startup_raises() -> None:
    orch = Orchestrator(LLMConfig(api_key=""), "postgresql://unused")
    with pytest.raises(RuntimeError):
        async for _ in orch.stream_chat(content="hi", run_id="r", session_id="s", user_id="u"):
            pass
