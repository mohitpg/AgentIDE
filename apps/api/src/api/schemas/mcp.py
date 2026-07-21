"""Pydantic DTOs for the /mcp registry API"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MCPServerBase(BaseModel):
    """Fields shared by create/read (everything the manager needs to connect)."""

    name: str = ""
    description: str | None = None
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    enabled: bool = True
    allowed_agents: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)


class MCPServerCreate(MCPServerBase):
    """Register a new MCP server. `id` is the stable key + tool namespace."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class MCPServerUpdate(BaseModel):
    """Partial update — only provided fields change (enable, reassign, etc.)."""

    name: str | None = None
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    enabled: bool | None = None
    allowed_agents: list[str] | None = None
    tool_allowlist: list[str] | None = None


class MCPServerRead(MCPServerBase):
    """A registered server. Mirrors `mcp_manager.MCPServerConfig` + timestamps."""

    id: str
    created_at: datetime
    updated_at: datetime


class MCPToolRead(BaseModel):
    """A tool advertised by a server, from live discovery."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPDiscoverResponse(BaseModel):
    """Result of connecting to one server and listing its tools."""

    server_id: str
    connected: bool
    tools: list[MCPToolRead] = Field(default_factory=list)
    error: str | None = None
