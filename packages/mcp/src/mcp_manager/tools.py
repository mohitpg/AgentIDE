"""
Exposing MCP tools to LLM agents.

Tool names are namespaced as ``{server_id}__{tool_name}`` so two servers can
advertise a ``read_file`` without colliding, and the manager can route a call
back to the right connection from the name alone.

`to_openai_tool` renders an exposed tool in the OpenAI function-calling shape,
which is what LangChain's `ChatModel.bind_tools` accepts directly — no Pydantic
arg-schema generation needed; the MCP server's own JSON Schema is passed through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_manager.connection import MCPToolInfo

NAME_SEP = "__"


@dataclass(frozen=True)
class ExposedTool:
    """One server tool as offered to agents, under a namespaced name."""

    exposed_name: str  # f"{server_id}{NAME_SEP}{tool.name}"
    server_id: str
    tool: MCPToolInfo


def exposed_name(server_id: str, tool_name: str) -> str:
    return f"{server_id}{NAME_SEP}{tool_name}"


def to_openai_tool(et: ExposedTool) -> dict[str, Any]:
    """Render an exposed tool as an OpenAI-format function tool for bind_tools."""
    schema = et.tool.input_schema or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": et.exposed_name,
            "description": et.tool.description or et.tool.name,
            "parameters": schema,
        },
    }
