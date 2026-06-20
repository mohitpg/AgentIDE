"""
MCPManager — server lifecycle, capability discovery, per-agent tool exposure.

Given a set of `MCPServerConfig`s the manager:
  - connects every enabled server (`connect`) and caches its tools,
  - resolves, per agent, which tools are exposed (server `allowed_agents` +
    per-server `tool_allowlist`),
  - renders those tools for `bind_tools` (`openai_tools_for_agent`),
  - routes a namespaced tool call back to the owning connection (`call`),
  - tears every connection down (`aclose`).

Connecting is best-effort: a server that fails to start is logged, recorded in
`status()`, and simply contributes no tools — one broken MCP server never takes
the agent down (graceful degradation, ARCHITECTURE §4).

The `connector` seam (default `connect_server`) lets tests inject fake
connections instead of spawning subprocesses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp_manager.config import MCPServerConfig
from mcp_manager.connection import MCPToolInfo, connect_server
from mcp_manager.tools import ExposedTool, exposed_name, to_openai_tool

logger = logging.getLogger(__name__)


class ConnectionLike(Protocol):
    """The slice of `MCPConnection` the manager depends on (so tests can fake it)."""

    config: MCPServerConfig

    async def list_tools(self) -> list[MCPToolInfo]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...
    async def aclose(self) -> None: ...


Connector = Callable[[MCPServerConfig], Awaitable[ConnectionLike]]


@dataclass
class ServerStatus:
    """A snapshot of one server for the settings UI / discovery endpoints."""

    id: str
    name: str
    transport: str
    enabled: bool
    connected: bool
    tools: list[str] = field(default_factory=list)
    error: str | None = None


class MCPManager:
    def __init__(
        self,
        configs: list[MCPServerConfig],
        *,
        connector: Connector = connect_server,
    ) -> None:
        self._configs: dict[str, MCPServerConfig] = {c.id: c for c in configs}
        self._connector = connector
        self._connections: dict[str, ConnectionLike] = {}
        self._tools: dict[str, list[MCPToolInfo]] = {}
        self._errors: dict[str, str] = {}

    @property
    def configs(self) -> list[MCPServerConfig]:
        return list(self._configs.values())

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def connect(self) -> None:
        """Connect every enabled, not-yet-connected server. Best-effort."""
        for cfg in self._configs.values():
            if cfg.enabled and cfg.id not in self._connections:
                await self._connect_one(cfg)

    async def _connect_one(self, cfg: MCPServerConfig) -> bool:
        try:
            conn = await self._connector(cfg)
            tools = await conn.list_tools()
        except Exception as exc:  # broken server must not break the agent
            logger.warning("MCP server '%s' failed to connect: %s", cfg.id, exc)
            self._errors[cfg.id] = str(exc)
            return False
        self._connections[cfg.id] = conn
        self._tools[cfg.id] = tools
        self._errors.pop(cfg.id, None)
        logger.info("MCP server '%s' connected (%d tools)", cfg.id, len(tools))
        return True

    async def aclose(self) -> None:
        for sid, conn in list(self._connections.items()):
            try:
                await conn.aclose()
            except Exception as exc:  # keep closing the rest
                logger.warning("error closing MCP server '%s': %s", sid, exc)
        self._connections.clear()
        self._tools.clear()

    # ── discovery / exposure ──────────────────────────────────────────────────
    def exposed_tools(self) -> list[ExposedTool]:
        """All connected tools that pass their server's tool allow-list."""
        out: list[ExposedTool] = []
        for sid, tools in self._tools.items():
            cfg = self._configs[sid]
            for tool in tools:
                if cfg.allows_tool(tool.name):
                    out.append(ExposedTool(exposed_name(sid, tool.name), sid, tool))
        return out

    def tools_for_agent(self, agent_id: str) -> list[ExposedTool]:
        """Exposed tools from servers this agent is allowed to use."""
        return [
            et
            for et in self.exposed_tools()
            if self._configs[et.server_id].allows_agent(agent_id)
        ]

    def openai_tools_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Agent's tools rendered for `ChatModel.bind_tools`."""
        return [to_openai_tool(et) for et in self.tools_for_agent(agent_id)]

    # ── invocation ────────────────────────────────────────────────────────────
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke a tool the model asked for, on its owning connection.

        ``name`` is normally the namespaced ``{server}__{tool}`` we advertised,
        but some providers strip the namespace and return the bare tool name, so
        resolution falls back to a bare-name match (see ``_resolve``).
        """
        et = self._resolve(name)
        if et is None:
            raise KeyError(f"no exposed MCP tool '{name}'")
        conn = self._connections.get(et.server_id)
        if conn is None:  # pragma: no cover - resolve implies a live connection
            raise KeyError(f"MCP server '{et.server_id}' is not connected")
        return await conn.call_tool(et.tool.name, arguments or {})

    def _resolve(self, name: str) -> ExposedTool | None:
        """Resolve a tool-call name to an exposed tool.

        Tries the exact namespaced name first; if that misses (e.g. a provider
        dropped the ``{server}__`` prefix), falls back to matching the bare tool
        name. When two servers expose the same bare name the namespace is
        ambiguous, so we log and take the first.
        """
        exposed = self.exposed_tools()
        for et in exposed:
            if et.exposed_name == name:
                return et
        bare_matches = [et for et in exposed if et.tool.name == name]
        if not bare_matches:
            return None
        if len(bare_matches) > 1:
            logger.warning(
                "tool name '%s' is ambiguous across servers %s; using '%s'. "
                "The provider appears to strip the tool namespace.",
                name,
                [et.server_id for et in bare_matches],
                bare_matches[0].exposed_name,
            )
        return bare_matches[0]

    # ── status (for the /mcp endpoints + settings UI) ──────────────────────────
    def status(self) -> list[ServerStatus]:
        out: list[ServerStatus] = []
        for sid, cfg in self._configs.items():
            connected = sid in self._connections
            out.append(
                ServerStatus(
                    id=sid,
                    name=cfg.name or sid,
                    transport=str(cfg.transport),
                    enabled=cfg.enabled,
                    connected=connected,
                    tools=[t.name for t in self._tools.get(sid, [])],
                    error=self._errors.get(sid),
                )
            )
        return out
