"""
Agent registry — Postgres system-of-record + Redis cache.

Specialist agents POST their A2A Agent Card to ``/agents/register`` on startup
and heartbeat periodically.

All Redis access degrades gracefully: if Redis is down we fall back to Postgres
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import AgentRegistration

logger = logging.getLogger(__name__)

_CACHE_KEY = "registry:agents:live"


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    """Treat naive timestamps (e.g. from SQLite) as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def skill_ids(card: dict) -> list[str]:
    """Skill ids advertised on an A2A Agent Card."""
    return [s["id"] for s in card.get("skills", []) if isinstance(s, dict) and s.get("id")]


def tags(card: dict) -> list[str]:
    """Union of skill tags (the A2A card has no top-level tags), order-preserved."""
    out: list[str] = list(card.get("tags") or [])  # defensive: some cards add them
    for s in card.get("skills", []):
        if isinstance(s, dict):
            out.extend(s.get("tags") or [])
    return list(dict.fromkeys(out))


class AgentRegistry:
    """Read/write access to the agent registry, backed by Postgres + Redis."""

    def __init__(
        self,
        db: AsyncSession,
        redis: aioredis.Redis,
        *,
        ttl_seconds: int = 90,
        cache_ttl_seconds: int = 30,
    ) -> None:
        self._db = db
        self._redis = redis
        self._ttl = ttl_seconds
        self._cache_ttl = cache_ttl_seconds

    # ── writes ──────────────────────────────────────────────────────────────
    async def register(
        self, agent_id: str, card: dict, *, url: str | None = None
    ) -> dict:
        """Upsert an agent's registration and mark it freshly alive."""
        values = {
            "name": card.get("name", agent_id),
            "description": card.get("description"),
            "url": url or card.get("url", ""),
            "version": card.get("version", "0.1.0"),
            "card": card,
            "skill_ids": skill_ids(card),
            "tags": tags(card),
            "last_heartbeat": _now(),
        }
        row = await self._db.get(AgentRegistration, agent_id)
        if row is None:
            row = AgentRegistration(id=agent_id, **values)
            self._db.add(row)
        else:
            for key, val in values.items():
                setattr(row, key, val)
        await self._db.flush()
        await self._invalidate()
        return self._to_dict(row)

    async def heartbeat(self, agent_id: str) -> bool:
        """Bump liveness for an agent. Returns False if it isn't registered."""
        row = await self._db.get(AgentRegistration, agent_id)
        if row is None:
            return False

        was_stale = _now() - _as_aware(row.last_heartbeat) > timedelta(seconds=self._ttl)
        row.last_heartbeat = _now()
        await self._db.flush()
        if was_stale:
            await self._invalidate()
        return True

    async def deregister(self, agent_id: str) -> bool:
        """Remove an agent. Returns False if it wasn't registered."""
        row = await self._db.get(AgentRegistration, agent_id)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.flush()
        await self._invalidate()
        return True

    async def reap_stale(self) -> int:
        """Delete agents that have missed the liveness window. Returns count removed."""
        cutoff = _now() - timedelta(seconds=self._ttl)
        result = await self._db.execute(
            delete(AgentRegistration).where(AgentRegistration.last_heartbeat < cutoff)
        )
        await self._db.flush()
        await self._invalidate()
        return result.rowcount or 0

    # ── reads ───────────────────────────────────────────────────────────────
    async def list_agents(self, *, include_stale: bool = False) -> list[dict]:
        agents = await self._cached_agents()
        if include_stale:
            return agents
        return [a for a in agents if self._is_live(a)]

    async def discover(
        self, *, skill: str | None = None, tag: str | None = None
    ) -> list[dict]:
        """Live agents, optionally filtered to those advertising a skill / tag."""
        agents = await self.list_agents()
        if skill is not None:
            agents = [a for a in agents if skill in a["skill_ids"]]
        if tag is not None:
            agents = [a for a in agents if tag in a["tags"]]
        return agents

    async def get(self, agent_id: str) -> dict | None:
        for a in await self._cached_agents():
            if a["agent_id"] == agent_id:
                return a
        return None

    async def url_map(self) -> dict[str, str]:
        """{agent_id: url} for live agents — the supervisor's delegation directory."""
        return {a["agent_id"]: a["url"] for a in await self.list_agents()}

    # ── internals ───────────────────────────────────────────────────────────
    def _is_live(self, agent: dict) -> bool:
        beat = _as_aware(datetime.fromisoformat(agent["last_heartbeat"]))
        return _now() - beat <= timedelta(seconds=self._ttl)

    def _to_dict(self, row: AgentRegistration) -> dict:
        return {
            "agent_id": row.id,
            "name": row.name,
            "description": row.description,
            "url": row.url,
            "version": row.version,
            "card": row.card,
            "skill_ids": list(row.skill_ids or []),
            "tags": list(row.tags or []),
            "last_heartbeat": _as_aware(row.last_heartbeat).isoformat(),
        }

    async def _load_from_db(self) -> list[dict]:
        result = await self._db.execute(
            select(AgentRegistration).order_by(AgentRegistration.id)
        )
        return [self._to_dict(r) for r in result.scalars().all()]

    async def _cached_agents(self) -> list[dict]:
        try:
            raw = await self._redis.get(_CACHE_KEY)
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:  # Redis down — serve from Postgres
            logger.warning("registry cache read failed (%s); using Postgres", exc)
            return await self._load_from_db()

        agents = await self._load_from_db()
        try:
            await self._redis.set(_CACHE_KEY, json.dumps(agents), ex=self._cache_ttl)
        except Exception as exc:
            logger.warning("registry cache write failed (%s)", exc)
        return agents

    async def _invalidate(self) -> None:
        try:
            await self._redis.delete(_CACHE_KEY)
        except Exception as exc:
            logger.warning("registry cache invalidation failed (%s)", exc)
