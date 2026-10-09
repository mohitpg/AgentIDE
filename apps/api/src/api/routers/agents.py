"""
/agents – the agent registry

Specialist A2A agents self-register here on startup and heartbeat to stay live;
the supervisor discovers them by skill. Backed by `AgentRegistry`
(Postgres + Redis).

  POST   /agents/register             register / update an agent's card
  POST   /agents/{agent_id}/heartbeat keep an agent marked live
  DELETE /agents/{agent_id}           deregister an agent
  GET    /agents                      list live agents (?include_stale=true for all)
  GET    /agents/discover             list live agents filtered by ?skill= / ?tag=

Registration, heartbeat, and deregistration are service-only (agents send the
shared A2A_AUTH_TOKEN); listing is open to any authenticated caller.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db.redis import get_redis
from api.db.session import get_db
from api.deps import get_principal, require_service
from api.schemas.agents import AgentInfo, AgentRegisterRequest
from api.services.registry import AgentRegistry

router = APIRouter()


def get_registry(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AgentRegistry:
    settings = get_settings()
    return AgentRegistry(
        db,
        redis,
        ttl_seconds=settings.registry_ttl_seconds,
        cache_ttl_seconds=settings.registry_cache_ttl_seconds,
    )


@router.post("/register", response_model=AgentInfo, dependencies=[Depends(require_service)])
async def register_agent(
    payload: AgentRegisterRequest,
    registry: AgentRegistry = Depends(get_registry),
) -> AgentInfo:
    record = await registry.register(payload.agent_id, payload.card, url=payload.url)
    return AgentInfo.from_record(record)


@router.post(
    "/{agent_id}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_service)],
)
async def heartbeat_agent(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> None:
    if not await registry.heartbeat(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not registered")


@router.delete(
    "/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_service)]
)
async def deregister_agent(
    agent_id: str,
    registry: AgentRegistry = Depends(get_registry),
) -> None:
    if not await registry.deregister(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not registered")


@router.get("", response_model=list[AgentInfo], dependencies=[Depends(get_principal)])
async def list_agents(
    include_stale: bool = Query(False),
    registry: AgentRegistry = Depends(get_registry),
) -> list[AgentInfo]:
    records = await registry.list_agents(include_stale=include_stale)
    return [AgentInfo.from_record(r) for r in records]


@router.get("/discover", response_model=list[AgentInfo], dependencies=[Depends(get_principal)])
async def discover_agents(
    skill: str | None = Query(None),
    tag: str | None = Query(None),
    registry: AgentRegistry = Depends(get_registry),
) -> list[AgentInfo]:
    records = await registry.discover(skill=skill, tag=tag)
    return [AgentInfo.from_record(r) for r in records]
