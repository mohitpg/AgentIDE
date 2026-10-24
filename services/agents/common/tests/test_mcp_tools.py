"""
Agent-side MCP: the tool-calling loop, and the relay to the user's desktop.

A minimal duck-typed `FakeModel` scripts the LLM's turns (tool call → answer).
A fake connector backs a real `MCPManager` so no subprocess is spawned, and an
`httpx.MockTransport` stands in for the gateway's relay routes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from agent_common.config import AgentSettings
from agent_common.mcp_tools import RelayToolManager, relay_tools_for_request, run_tool_loop
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from mcp_manager import MCPManager, MCPServerConfig
from mcp_manager.connection import MCPToolInfo

pytestmark = pytest.mark.asyncio


class FakeModel:
    """Scripts AIMessages for ainvoke; streams the next script item for astream."""

    def __init__(self, scripted: list[AIMessage]) -> None:
        self._scripted = list(scripted)
        self.bound_tools: list | None = None
        self.seen: list = []  # the messages of the latest ainvoke

    def bind_tools(self, tools: list) -> FakeModel:
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list) -> AIMessage:
        self.seen = list(messages)
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


# ── RelayToolManager: the user's desktop tools via the gateway ──────────────────
_USER = uuid.uuid4()
_READ_TOOL = {
    "name": "filesystem__read_text_file",
    "server_id": "filesystem",
    "tool": "read_text_file",
    "description": "read a file",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}


class FakeGateway:
    """The relay routes: records requests, serves the tool list, answers calls."""

    def __init__(self, *, reply: dict | None = None, status: int = 200) -> None:
        self.reply = reply or {"ok": True, "content": "print('hi')"}
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        if request.url.path == "/mcp/relay/tools":
            return httpx.Response(200, json=[_READ_TOOL])
        if request.url.path == "/mcp/relay/call":
            return httpx.Response(200, json=self.reply)
        return httpx.Response(404)


def _relay(gateway: FakeGateway) -> RelayToolManager:
    return RelayToolManager(
        gateway_url="http://gw",
        auth_token="svc",
        user_id=_USER,
        agent_id="code",
        transport=httpx.MockTransport(gateway),
    )


def _read_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "filesystem__read_text_file", "args": {"path": "a.py"}, "id": "t1"}],
    )


async def test_relay_loads_tools_on_behalf_of_the_user():
    gateway = FakeGateway()
    mgr = await _relay(gateway).load()

    tools = mgr.openai_tools_for_agent("code")
    assert [t["function"]["name"] for t in tools] == ["filesystem__read_text_file"]
    request = gateway.requests[0]
    assert request.url.params["agent_id"] == "code"
    assert request.headers["X-On-Behalf-Of"] == str(_USER)
    assert request.headers["Authorization"] == "Bearer svc"


async def test_relay_unreachable_means_no_tools():
    mgr = await _relay(FakeGateway(status=503)).load()
    assert mgr.openai_tools_for_agent("code") == []


async def test_relay_runs_tool_in_the_loop():
    gateway = FakeGateway()
    mgr = await _relay(gateway).load()
    model = FakeModel([_read_call(), AIMessage(content="it prints hi")])

    out = [
        c
        async for c in run_tool_loop(
            model=model, manager=mgr, agent_id="code", system_prompt="sys", user_input="a.py?"
        )
    ]
    assert "".join(out) == "it prints hi"
    call = gateway.requests[-1]
    assert call.url.path == "/mcp/relay/call"
    assert json.loads(call.content) == {
        "agent_id": "code",
        "name": "filesystem__read_text_file",
        "arguments": {"path": "a.py"},
    }
    assert model.seen[-1].content == "print('hi')"


async def test_relay_refusal_is_fed_back_to_the_model():
    gateway = FakeGateway(reply={"ok": False, "error": "The user declined this tool call."})
    mgr = await _relay(gateway).load()
    model = FakeModel([_read_call(), AIMessage(content="ok, I won't")])

    out = [
        c
        async for c in run_tool_loop(
            model=model, manager=mgr, agent_id="code", system_prompt="sys", user_input="a.py?"
        )
    ]
    assert "".join(out) == "ok, I won't"
    result = model.seen[-1]
    assert isinstance(result, ToolMessage)
    assert "The user declined this tool call." in result.content


async def test_no_request_scope_means_no_relay():
    assert await relay_tools_for_request("code", AgentSettings()) is None
