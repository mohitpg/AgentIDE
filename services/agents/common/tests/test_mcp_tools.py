"""
Agent-side MCP: the tool-calling loop and config loading.

A minimal duck-typed `FakeModel` scripts the LLM's turns (tool call → answer),
and a fake connector backs a real `MCPManager` so no subprocess is spawned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from agent_common import mcp_tools
from agent_common.mcp_tools import build_agent_manager, run_tool_loop
from langchain_core.messages import AIMessage, AIMessageChunk
from mcp_manager import MCPManager, MCPServerConfig
from mcp_manager.connection import MCPToolInfo

pytestmark = pytest.mark.asyncio


class FakeModel:
    """Scripts AIMessages for ainvoke; streams the next script item for astream."""

    def __init__(self, scripted: list[AIMessage]) -> None:
        self._scripted = list(scripted)
        self.bound_tools: list | None = None

    def bind_tools(self, tools: list) -> FakeModel:
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list) -> AIMessage:
        return self._scripted.pop(0)

    async def astream(self, messages: list) -> AsyncIterator[AIMessageChunk]:
        msg = self._scripted.pop(0)
        yield AIMessageChunk(content=msg.content)


class FakeConnection:
    def __init__(self, config: MCPServerConfig, tools: list[MCPToolInfo]) -> None:
        self.config = config
        self._tools = tools
        self.calls: list[tuple[str, dict]] = []
        self.fail = False

    async def list_tools(self) -> list[MCPToolInfo]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("boom")
        return f"contents-of-{arguments.get('path', '?')}"

    async def aclose(self) -> None:
        pass


def _manager_with_read_file() -> tuple[MCPManager, dict[str, FakeConnection]]:
    created: dict[str, FakeConnection] = {}
    tool = MCPToolInfo(
        name="read_file",
        description="read a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    async def connector(cfg: MCPServerConfig) -> FakeConnection:
        conn = FakeConnection(cfg, [tool])
        created[cfg.id] = conn
        return conn

    mgr = MCPManager([MCPServerConfig(id="filesystem")], connector=connector)
    return mgr, created


async def test_no_tools_streams_plain_answer():
    mgr = MCPManager([])  # no servers → no tools
    model = FakeModel([AIMessage(content="just answering")])
    out = [
        c
        async for c in run_tool_loop(
            model=model,
            manager=mgr,
            agent_id="code",
            system_prompt="sys",
            user_input="hi",
        )
    ]
    assert "".join(out) == "just answering"
    assert model.bound_tools is None  # never bound — nothing to bind


async def test_executes_tool_then_answers():
    mgr, created = _manager_with_read_file()
    await mgr.connect()
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "filesystem__read_file", "args": {"path": "a.py"}, "id": "t1"}
                ],
            ),
            AIMessage(content="the file defines f()"),
        ]
    )
    out = [
        c
        async for c in run_tool_loop(
            model=model,
            manager=mgr,
            agent_id="code",
            system_prompt="sys",
            user_input="explain a.py",
        )
    ]
    assert "".join(out) == "the file defines f()"
    # The de-namespaced tool ran on the filesystem connection.
    assert created["filesystem"].calls == [("read_file", {"path": "a.py"})]
    # Tools were bound for the agent.
    assert model.bound_tools and model.bound_tools[0]["function"]["name"] == (
        "filesystem__read_file"
    )


async def test_tool_error_is_fed_back_not_raised():
    mgr, created = _manager_with_read_file()
    await mgr.connect()
    created["filesystem"].fail = True
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "filesystem__read_file", "args": {"path": "x"}, "id": "t1"}
                ],
            ),
            AIMessage(content="sorry, that file errored"),
        ]
    )
    out = [
        c
        async for c in run_tool_loop(
            model=model,
            manager=mgr,
            agent_id="code",
            system_prompt="sys",
            user_input="read x",
        )
    ]
    assert "".join(out) == "sorry, that file errored"


async def test_build_agent_manager_falls_back_to_builtins(monkeypatch):
    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(mcp_tools, "fetch_agent_mcp_configs", _empty)
    mgr = await build_agent_manager(
        agent_id="code",
        gateway_url="http://gw",
        fallback_workspace="/work",
    )
    assert {c.id for c in mgr.configs} == {"filesystem", "git", "fetch"}
