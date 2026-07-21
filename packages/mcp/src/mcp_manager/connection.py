"""
A live connection to one MCP server.

`connect_server(config)` opens the transport (stdio subprocess or SSE), performs
the MCP `initialize` handshake, and returns an `MCPConnection` that can list and
call tools.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_manager.config import MCPServerConfig, MCPTransport

logger = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    """An MCP tool returned an error result (``isError``)."""


@dataclass(frozen=True)
class MCPToolInfo:
    """A tool advertised by a server: name, description, JSON-Schema inputs."""

    name: str
    description: str
    input_schema: dict[str, Any]


def _result_text(result: Any) -> str:
    """Flatten a `CallToolResult`'s content blocks into plain text."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:  # non-text block (image/resource) — keep a readable placeholder
            parts.append(str(getattr(block, "data", block)))
    return "\n".join(parts)


class MCPConnection:
    """An initialized MCP session plus the resources keeping it alive."""

    def __init__(
        self,
        config: MCPServerConfig,
        session: ClientSession,
        stack: AsyncExitStack,
    ) -> None:
        self._config = config
        self._session = session
        self._stack = stack

    @property
    def config(self) -> MCPServerConfig:
        return self._config

    async def list_tools(self) -> list[MCPToolInfo]:
        result = await self._session.list_tools()
        return [
            MCPToolInfo(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = await self._session.call_tool(name, arguments or {})
        text = _result_text(result)
        if getattr(result, "isError", False):
            raise MCPToolError(text or f"tool '{name}' returned an error")
        return text

    async def aclose(self) -> None:
        await self._stack.aclose()


async def connect_server(config: MCPServerConfig) -> MCPConnection:
    """Open + initialize a connection to one MCP server.

    Raises if the transport can't be opened or the handshake fails; the stack is
    always unwound on failure so a half-open subprocess never leaks.
    """
    stack = AsyncExitStack()
    try:
        if config.transport == MCPTransport.STDIO:
            if not config.command:
                raise ValueError(f"stdio server '{config.id}' has no command")
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                # Inherit the parent env (PATH, APPDATA, …) so npx/uvx resolve,
                # then layer the server's overrides on top.
                env={**os.environ, **config.env} if config.env else None,
                cwd=config.cwd,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif config.transport == MCPTransport.SSE:
            from mcp.client.sse import sse_client

            if not config.url:
                raise ValueError(f"sse server '{config.id}' has no url")
            read, write = await stack.enter_async_context(sse_client(config.url))
        else:  # pragma: no cover - guarded by the enum
            raise ValueError(f"unknown transport '{config.transport}'")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
    except BaseException:
        await stack.aclose()
        raise

    return MCPConnection(config, session, stack)
