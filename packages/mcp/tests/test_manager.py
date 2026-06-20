"""
MCPManager: connection, per-agent exposure, dispatch, graceful degradation.

A `FakeConnection` stands in for a real MCP session so these run with no
subprocesses or network.
"""

from __future__ import annotations

import pytest
from mcp_manager.config import MCPServerConfig
from mcp_manager.connection import MCPToolInfo
from mcp_manager.manager import MCPManager


class FakeConnection:
    def __init__(self, config: MCPServerConfig, tools: list[MCPToolInfo]) -> None:
        self.config = config
        self._tools = tools
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def list_tools(self) -> list[MCPToolInfo]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return f"{name}({arguments})"

    async def aclose(self) -> None:
        self.closed = True


def _tool(name: str) -> MCPToolInfo:
    return MCPToolInfo(
        name=name,
        description=f"{name} desc",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def _manager(configs, tools_by_id):
    created: dict[str, FakeConnection] = {}

    async def connector(cfg: MCPServerConfig) -> FakeConnection:
        conn = FakeConnection(cfg, tools_by_id.get(cfg.id, []))
        created[cfg.id] = conn
        return conn

    return MCPManager(configs, connector=connector), created


async def test_connect_discovers_tools_and_namespaces_them():
    cfgs = [MCPServerConfig(id="filesystem"), MCPServerConfig(id="git")]
    mgr, _ = _manager(cfgs, {"filesystem": [_tool("read_file")], "git": [_tool("status")]})
    await mgr.connect()

    names = {et.exposed_name for et in mgr.exposed_tools()}
    assert names == {"filesystem__read_file", "git__status"}


async def test_disabled_server_is_not_connected():
    cfgs = [MCPServerConfig(id="git", enabled=False)]
    mgr, created = _manager(cfgs, {"git": [_tool("status")]})
    await mgr.connect()
    assert created == {}
    assert mgr.exposed_tools() == []


async def test_per_agent_allow_list():
    cfgs = [
        MCPServerConfig(id="filesystem", allowed_agents=["code"]),
        MCPServerConfig(id="fetch", allowed_agents=["web"]),
    ]
    mgr, _ = _manager(cfgs, {"filesystem": [_tool("read_file")], "fetch": [_tool("fetch")]})
    await mgr.connect()

    code_tools = {et.exposed_name for et in mgr.tools_for_agent("code")}
    web_tools = {et.exposed_name for et in mgr.tools_for_agent("web")}
    assert code_tools == {"filesystem__read_file"}
    assert web_tools == {"fetch__fetch"}


async def test_tool_allowlist_filters_within_server():
    cfgs = [MCPServerConfig(id="filesystem", tool_allowlist=["read_file"])]
    mgr, _ = _manager(
        cfgs, {"filesystem": [_tool("read_file"), _tool("write_file")]}
    )
    await mgr.connect()
    assert {et.tool.name for et in mgr.exposed_tools()} == {"read_file"}


async def test_openai_tools_shape():
    cfgs = [MCPServerConfig(id="git")]
    mgr, _ = _manager(cfgs, {"git": [_tool("status")]})
    await mgr.connect()
    (spec,) = mgr.openai_tools_for_agent("code")
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "git__status"
    assert spec["function"]["parameters"]["type"] == "object"


async def test_call_routes_to_owning_connection():
    cfgs = [MCPServerConfig(id="git")]
    mgr, created = _manager(cfgs, {"git": [_tool("status")]})
    await mgr.connect()

    out = await mgr.call("git__status", {"path": "."})
    assert out == "status({'path': '.'})"
    # Routed with the de-namespaced tool name.
    assert created["git"].calls == [("status", {"path": "."})]


async def test_call_unknown_tool_raises():
    mgr, _ = _manager([MCPServerConfig(id="git")], {"git": [_tool("status")]})
    await mgr.connect()
    with pytest.raises(KeyError):
        await mgr.call("git__nope", {})


async def test_call_resolves_bare_name_when_provider_strips_namespace():
    # Some OpenRouter providers (e.g. poolside/laguna) drop the "{server}__"
    # prefix and return just the bare tool name in the structured call.
    cfgs = [MCPServerConfig(id="filesystem")]
    mgr, created = _manager(cfgs, {"filesystem": [_tool("read_text_file")]})
    await mgr.connect()

    out = await mgr.call("read_text_file", {"path": "x.py"})
    assert out == "read_text_file({'path': 'x.py'})"
    assert created["filesystem"].calls == [("read_text_file", {"path": "x.py"})]


async def test_bare_name_resolution_handles_collision():
    # Two servers expose a tool with the same bare name; a stripped call still
    # resolves (to the first) rather than failing.
    cfgs = [MCPServerConfig(id="filesystem"), MCPServerConfig(id="memory")]
    mgr, _ = _manager(
        cfgs, {"filesystem": [_tool("read_file")], "memory": [_tool("read_file")]}
    )
    await mgr.connect()

    # Namespaced calls remain unambiguous.
    assert await mgr.call("memory__read_file", {}) == "read_file({})"
    # A bare call resolves to one of them without raising.
    assert (await mgr.call("read_file", {})).startswith("read_file(")


async def test_failed_server_degrades_gracefully():
    async def connector(cfg: MCPServerConfig):
        raise RuntimeError("npx not found")

    mgr = MCPManager([MCPServerConfig(id="filesystem")], connector=connector)
    await mgr.connect()  # must not raise

    assert mgr.exposed_tools() == []
    (status,) = mgr.status()
    assert status.connected is False
    assert status.error == "npx not found"


async def test_aclose_closes_all_connections():
    cfgs = [MCPServerConfig(id="filesystem"), MCPServerConfig(id="git")]
    mgr, created = _manager(cfgs, {"filesystem": [_tool("read_file")], "git": [_tool("status")]})
    await mgr.connect()
    await mgr.aclose()
    assert all(c.closed for c in created.values())
    assert mgr.exposed_tools() == []
