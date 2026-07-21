"""
/mcp — MCP server registry + live capability discovery.

The settings-driven plug-in surface: register MCP servers and assign them to
agents, no code changes. Agents read their assignments from here on startup and
connect the servers themselves (they are the MCP clients); the gateway stores
configs and does on-demand discovery.

  GET    /mcp                 list registered servers
  POST   /mcp                 register a server
  GET    /mcp/builtins        built-in templates (filesystem, git, web-fetch)
  GET    /mcp/agent/{id}      servers assigned to an agent (agents call this)
  GET    /mcp/{id}            get one server
  PATCH  /mcp/{id}            update (enable, reassign agents, …)
  DELETE /mcp/{id}            remove a server
  POST   /mcp/{id}/discover   connect the server and list its tools (live)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from mcp_manager import builtin_servers, connect_server
from mcp_manager.config import MCPServerConfig
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db.session import get_db
from api.schemas.mcp import (
    MCPDiscoverResponse,
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPToolRead,
)
from api.services.mcp_servers import MCPServerStore, to_config

logger = logging.getLogger(__name__)
router = APIRouter()


def get_store(db: AsyncSession = Depends(get_db)) -> MCPServerStore:
    return MCPServerStore(db)


@router.get("", response_model=list[MCPServerRead])
async def list_servers(store: MCPServerStore = Depends(get_store)) -> list[MCPServerRead]:
    return [MCPServerRead.model_validate(r, from_attributes=True) for r in await store.list()]


@router.post("", response_model=MCPServerRead, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: MCPServerCreate, store: MCPServerStore = Depends(get_store)
) -> MCPServerRead:
    if await store.get(payload.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"server '{payload.id}' already exists")
    row = await store.create(payload)
    return MCPServerRead.model_validate(row, from_attributes=True)


@router.get("/builtins", response_model=list[MCPServerCreate])
async def list_builtins() -> list[MCPServerCreate]:
    """Templates the settings UI can one-click register (filesystem/git/fetch)."""
    workspace = get_settings().mcp_workspace_dir or "."
    return [
        MCPServerCreate(**cfg.model_dump(mode="json")) for cfg in builtin_servers(workspace)
    ]


@router.get("/agent/{agent_id}", response_model=list[MCPServerRead])
async def servers_for_agent(
    agent_id: str, store: MCPServerStore = Depends(get_store)
) -> list[MCPServerRead]:
    """Enabled servers assigned to an agent — what the agent connects on startup."""
    rows = [r for r in await store.list() if r.enabled and to_config(r).allows_agent(agent_id)]
    return [MCPServerRead.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{server_id}", response_model=MCPServerRead)
async def get_server(
    server_id: str, store: MCPServerStore = Depends(get_store)
) -> MCPServerRead:
    row = await store.get(server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    return MCPServerRead.model_validate(row, from_attributes=True)


@router.patch("/{server_id}", response_model=MCPServerRead)
async def update_server(
    server_id: str,
    payload: MCPServerUpdate,
    store: MCPServerStore = Depends(get_store),
) -> MCPServerRead:
    row = await store.update(server_id, payload)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    return MCPServerRead.model_validate(row, from_attributes=True)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str, store: MCPServerStore = Depends(get_store)
) -> None:
    if not await store.delete(server_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")


@router.post("/{server_id}/discover", response_model=MCPDiscoverResponse)
async def discover_server(
    server_id: str, store: MCPServerStore = Depends(get_store)
) -> MCPDiscoverResponse:
    """Connect to the server and list its tools (verifies it works end-to-end)."""
    row = await store.get(server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    return await _discover(to_config(row))


async def _discover(config: MCPServerConfig) -> MCPDiscoverResponse:
    try:
        conn = await connect_server(config)
    except Exception as exc:  # bad command, missing runtime, server crash …
        logger.warning("MCP discover for '%s' failed: %s", config.id, exc)
        return MCPDiscoverResponse(server_id=config.id, connected=False, error=str(exc))
    try:
        tools = await conn.list_tools()
        return MCPDiscoverResponse(
            server_id=config.id,
            connected=True,
            tools=[
                MCPToolRead(name=t.name, description=t.description, input_schema=t.input_schema)
                for t in tools
            ],
        )
    finally:
        await conn.aclose()
