"""
/workspace — the current working directory the agents are rooted in.

  GET /workspace   → the current workspace (absolute path + display name)
  PUT /workspace   → switch to another directory

Switching is runtime-only (see ``api.services.workspace``): it updates the value
the built-in MCP templates read, then best-effort notifies every live agent's
``POST /admin/workspace`` so workspace-aware agents (the code agent) rebuild their
filesystem/git tools rooted at the new directory without a restart.

The workspace is host-wide (every user's code-agent turns run in it), so any
signed-in user can read it but only admins can switch it.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.config import get_settings
from api.db.redis import redis_client
from api.db.session import AsyncSessionLocal
from api.deps import get_current_user, require_admin
from api.schemas.workspace import WorkspaceRead, WorkspaceUpdate
from api.services.registry import AgentRegistry
from api.services.workspace import get_workspace, set_workspace

logger = logging.getLogger(__name__)
router = APIRouter()


def _read(path: str) -> WorkspaceRead:
    # basename, but fall back to the whole path for a drive root like "C:\".
    return WorkspaceRead(path=path, name=os.path.basename(path.rstrip("/\\")) or path)


async def _live_agent_urls() -> dict[str, str]:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        registry = AgentRegistry(
            db,
            redis_client,
            ttl_seconds=settings.registry_ttl_seconds,
            cache_ttl_seconds=settings.registry_cache_ttl_seconds,
        )
        return await registry.url_map()


async def _broadcast(path: str) -> None:
    """Tell every live agent about the new workspace (best-effort, never fatal)."""
    urls = await _live_agent_urls()
    token = get_settings().a2a_auth_token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as http:
        for agent_id, url in urls.items():
            endpoint = f"{url.rstrip('/')}/admin/workspace"
            try:
                resp = await http.post(endpoint, json={"path": path})
                resp.raise_for_status()
            except Exception as exc:  # agent down / not workspace-aware / slow
                logger.warning("workspace update to agent '%s' failed: %s", agent_id, exc)


@router.get("", response_model=WorkspaceRead, dependencies=[Depends(get_current_user)])
async def read_workspace() -> WorkspaceRead:
    return _read(get_workspace())


@router.put("", response_model=WorkspaceRead, dependencies=[Depends(require_admin)])
async def change_workspace(payload: WorkspaceUpdate) -> WorkspaceRead:
    try:
        path = set_workspace(payload.path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _broadcast(path)
    return _read(path)
