"""Pydantic DTOs for the MCP bridge (desktop ↔ gateway) and relay (agent → gateway)."""

from __future__ import annotations

from typing import Any

from mcp_manager import NAME_SEP, MCPServerConfig
from pydantic import BaseModel, Field, field_validator


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


class InventoryTool(BaseModel):
    """One tool a desktop MCP server advertises."""

    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=_empty_schema)


class InventoryServer(BaseModel):
    """A running MCP server on the user's desktop: its id, exposure and tools.

    The desktop never says how a server is launched, only what it offers.
    """

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = ""
    allowed_agents: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)
    tools: list[InventoryTool] = Field(default_factory=list, max_length=500)

    @field_validator("id")
    @classmethod
    def _no_separator(cls, value: str) -> str:
        # The id namespaces tool names as {id}__{tool}.
        if NAME_SEP in value:
            raise ValueError(f"server id must not contain '{NAME_SEP}'")
        return value

    def exposure(self) -> MCPServerConfig:
        """The exposure rules as a manager config (`allows_agent` / `allows_tool`)."""
        return MCPServerConfig(
            id=self.id,
            name=self.name,
            allowed_agents=self.allowed_agents,
            tool_allowlist=self.tool_allowlist,
        )


class RelayTool(BaseModel):
    """A desktop tool exposed to one agent, under its namespaced name."""

    name: str  # {server_id}__{tool}
    server_id: str
    tool: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=_empty_schema)


class RelayCallRequest(BaseModel):
    """An agent's tool call. `name` is as the model gave it: namespaced or bare."""

    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)


class RelayCallResult(BaseModel):
    """The tool's text output, or why it didn't run (offline, declined, timed out)."""

    ok: bool
    content: str = ""
    error: str | None = None
