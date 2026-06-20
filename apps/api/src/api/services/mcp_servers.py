"""
MCP server store — Postgres-backed registry for the /mcp API (Phase 6).

CRUD over the `mcp_servers` table plus `configs_for_agent`, which the agents call
(via ``GET /mcp/agent/{id}``) to learn which servers they may connect. A row maps
directly onto a `mcp_manager.MCPServerConfig`, the shape the manager connects and
the agent reconstructs.

Unlike the agent registry there's no Redis cache here: this is settings data read
on agent startup and in the (rare) settings UI, not on a per-request hot path.
"""

from __future__ import annotations

from mcp_manager import MCPServerConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.mcp import MCPServer
from api.schemas.mcp import MCPServerCreate, MCPServerUpdate


def to_config(row: MCPServer) -> MCPServerConfig:
    """Map a DB row onto a connectable manager config."""
    return MCPServerConfig(
        id=row.id,
        name=row.name,
        description=row.description or "",
        transport=row.transport,
        command=row.command,
        args=list(row.args or []),
        env=dict(row.env or {}),
        cwd=row.cwd,
        url=row.url,
        enabled=row.enabled,
        allowed_agents=list(row.allowed_agents or []),
        tool_allowlist=list(row.tool_allowlist or []),
    )


class MCPServerStore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(self) -> list[MCPServer]:
        result = await self._db.execute(select(MCPServer).order_by(MCPServer.id))
        return list(result.scalars().all())

    async def get(self, server_id: str) -> MCPServer | None:
        return await self._db.get(MCPServer, server_id)

    async def create(self, data: MCPServerCreate) -> MCPServer:
        row = MCPServer(**data.model_dump())
        self._db.add(row)
        await self._db.flush()
        await self._db.refresh(row)
        return row

    async def update(self, server_id: str, data: MCPServerUpdate) -> MCPServer | None:
        row = await self._db.get(MCPServer, server_id)
        if row is None:
            return None
        for key, val in data.model_dump(exclude_unset=True).items():
            setattr(row, key, val)
        await self._db.flush()
        await self._db.refresh(row)
        return row

    async def delete(self, server_id: str) -> bool:
        row = await self._db.get(MCPServer, server_id)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.flush()
        return True

    async def configs_for_agent(self, agent_id: str) -> list[MCPServerConfig]:
        """Enabled servers this agent is allowed to use, as manager configs."""
        rows = await self.list()
        configs = [to_config(r) for r in rows if r.enabled]
        return [c for c in configs if c.allows_agent(agent_id)]
