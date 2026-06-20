"""Config + built-in server defaults and exposure rules."""

from __future__ import annotations

from mcp_manager.config import (
    MCPServerConfig,
    MCPTransport,
    builtin_servers,
    filesystem_server,
)


def test_builtin_servers_cover_phase6_core():
    servers = builtin_servers("/work", repo="/work/repo")
    ids = {s.id for s in servers}
    assert ids == {"filesystem", "git", "fetch"}
    assert all(s.transport == MCPTransport.STDIO for s in servers)


def test_filesystem_server_scopes_to_workspace():
    fs = filesystem_server("/work")
    assert "@modelcontextprotocol/server-filesystem" in fs.args
    assert fs.args[-1] == "/work"


def test_allows_agent_empty_means_all():
    cfg = MCPServerConfig(id="git", allowed_agents=[])
    assert cfg.allows_agent("code")
    assert cfg.allows_agent("web")


def test_allows_agent_respects_list():
    cfg = MCPServerConfig(id="git", allowed_agents=["code"])
    assert cfg.allows_agent("code")
    assert not cfg.allows_agent("web")


def test_allows_tool_respects_allowlist():
    cfg = MCPServerConfig(id="fs", tool_allowlist=["read_file"])
    assert cfg.allows_tool("read_file")
    assert not cfg.allows_tool("write_file")


def test_config_round_trips_through_json():
    # The shape agents reconstruct from the gateway's JSON must survive a dump.
    original = MCPServerConfig(
        id="git",
        name="Git",
        command="uvx",
        args=["mcp-server-git"],
        allowed_agents=["code"],
    )
    restored = MCPServerConfig.model_validate(original.model_dump(mode="json"))
    assert restored == original
