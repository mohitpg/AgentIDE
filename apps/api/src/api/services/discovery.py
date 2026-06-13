"""
Registry → supervisor discovery bridge (Phase 2).

The supervisor graph routes by consuming an `orchestrator.AgentDirectory`
(``agent_id → base URL``). In Phase 1 that map was static; now it is built from
the live agent registry, so the supervisor delegates only to agents that are
actually registered and heartbeating.

`build_directory` is the single place that turns a registry snapshot into the
directory the graph already understands — swapping discovery never touches the
graph. Use `get_agent_directory` as a FastAPI dependency wherever the supervisor
is constructed for a request.
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
