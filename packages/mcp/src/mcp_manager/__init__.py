"""
agentide-mcp: MCP manager.

The plug-in surface for tools/resources. Adding a capability is config, not code:
register an MCP server (filesystem, git, web-fetch, …) and assign it to agents.

Pieces:
  - `MCPServerConfig` / `builtin_servers` — transport-agnostic server config.
  - `connect_server` / `MCPConnection`   — one live MCP session (stdio / SSE).
  - `MCPManager`                          — lifecycle, capability discovery, and
                                            per-agent tool exposure + dispatch.

Agents act as MCP clients: they load their assigned configs, run an `MCPManager`,
and bind the exposed tools onto their LLM. The gateway stores the configs and
does on-demand discovery; the manager itself is transport/storage agnostic.
"""

from __future__ import annotations

from mcp_manager.config import (
    MCPServerConfig,
    MCPTransport,
    builtin_servers,
    fetch_server,
    filesystem_server,
    git_server,
)
from mcp_manager.connection import (
    MCPConnection,
    MCPToolError,
    MCPToolInfo,
    connect_server,
)
from mcp_manager.manager import Connector, ExposedTool, MCPManager, ServerStatus
from mcp_manager.tools import NAME_SEP, exposed_name, to_openai_tool

__all__ = [
    "NAME_SEP",
    "Connector",
    "ExposedTool",
    "MCPConnection",
    "MCPManager",
    "MCPServerConfig",
    "MCPToolError",
    "MCPToolInfo",
    "MCPTransport",
    "ServerStatus",
    "builtin_servers",
    "connect_server",
    "exposed_name",
    "fetch_server",
    "filesystem_server",
    "git_server",
    "to_openai_tool",
]
