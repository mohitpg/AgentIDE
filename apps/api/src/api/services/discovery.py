"""
Registry → supervisor discovery bridge.

The supervisor graph routes by consuming an `orchestrator.AgentDirectory`
(``agent_id → base URL``)

"""

from __future__ import annotations

from fastapi import Depends
from orchestrator import AgentDirectory

from api.routers.agents import get_registry
from api.services.registry import AgentRegistry


async def build_directory(registry: AgentRegistry) -> AgentDirectory:
    """Snapshot the registry's live agents into an `AgentDirectory`."""
    return AgentDirectory(urls=await registry.url_map())


async def get_agent_directory(
    registry: AgentRegistry = Depends(get_registry),
) -> AgentDirectory:
    return await build_directory(registry)
