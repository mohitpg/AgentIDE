"""
MCP server configuration + built-in server definitions.

An `MCPServerConfig` is a transport-agnostic description of one MCP server:
how to launch/reach it, and who may use it. The deployed stack runs MCP servers
in the desktop app; the gateway uses this shape only for the exposure rules the
desktop reports with its inventory (`allows_agent` / `allows_tool`).

Exposure is two-layered so the settings UI can be coarse or fine-grained:
  - `allowed_agents` — which agents may use this server (empty = all),
  - `tool_allowlist` — which of the server's tools are exposed (empty = all).

"""

from __future__ import annotations

import sys
from enum import StrEnum

from pydantic import BaseModel, Field


class MCPTransport(StrEnum):
    STDIO = "stdio"  # launch a subprocess, talk over stdin/stdout
    SSE = "sse"  # connect to a running HTTP server over SSE


class MCPServerConfig(BaseModel):
    """How to reach one MCP server, and which agents/tools it exposes."""

    # Stable id, e.g. "filesystem" / "git". Must not contain the "__" separator
    # the manager uses to namespace tool names.
    id: str = Field(min_length=1, max_length=64)
    name: str = ""
    description: str = ""
    transport: MCPTransport = MCPTransport.STDIO

    # ── stdio transport ────────────────────────────────────────────────────────
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    # ── sse transport ──────────────────────────────────────────────────────────
    url: str = ""

    # ── exposure ───────────────────────────────────────────────────────────────
    enabled: bool = True
    allowed_agents: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)

    def allows_agent(self, agent_id: str) -> bool:
        """True if this server is exposed to ``agent_id`` (empty list = all)."""
        return not self.allowed_agents or agent_id in self.allowed_agents

    def allows_tool(self, tool_name: str) -> bool:
        """True if ``tool_name`` is exposed by this server (empty list = all)."""
        return not self.tool_allowlist or tool_name in self.tool_allowlist


# ── built-in core servers ──────────────────────────────────────────────────────


def _npx() -> str:
    return "npx.cmd" if sys.platform == "win32" else "npx"


def _uvx() -> str:
    return "uvx.cmd" if sys.platform == "win32" else "uvx"


def filesystem_server(
    workspace: str, *, allowed_agents: list[str] | None = None
) -> MCPServerConfig:
    """`@modelcontextprotocol/server-filesystem` scoped to ``workspace``.

    The server refuses any path outside the directories passed as args, so the
    workspace doubles as the sandbox boundary.
    """
    return MCPServerConfig(
        id="filesystem",
        name="Filesystem",
        description=f"Read/write files under {workspace}",
        transport=MCPTransport.STDIO,
        command=_npx(),
        args=["-y", "@modelcontextprotocol/server-filesystem", workspace],
        allowed_agents=list(allowed_agents or []),
    )


def git_server(
    repo: str, *, allowed_agents: list[str] | None = None
) -> MCPServerConfig:
    """`mcp-server-git` (uvx) operating on the ``repo`` working tree."""
    return MCPServerConfig(
        id="git",
        name="Git",
        description=f"Git operations on {repo}",
        transport=MCPTransport.STDIO,
        command=_uvx(),
        args=["mcp-server-git", "--repository", repo],
        allowed_agents=list(allowed_agents or []),
    )


def fetch_server(*, allowed_agents: list[str] | None = None) -> MCPServerConfig:
    """`mcp-server-fetch` (uvx) — fetch a URL and return cleaned markdown."""
    return MCPServerConfig(
        id="fetch",
        name="Web Fetch",
        description="Fetch a URL and extract its content as markdown",
        transport=MCPTransport.STDIO,
        command=_uvx(),
        args=["mcp-server-fetch"],
        allowed_agents=list(allowed_agents or []),
    )


def builtin_servers(
    workspace: str, *, repo: str | None = None
) -> list[MCPServerConfig]:
    return [
        filesystem_server(workspace),
        git_server(repo or workspace),
        fetch_server(),
    ]
