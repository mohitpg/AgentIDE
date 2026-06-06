"""
GET /health – reports status of API, database, and Redis.

Returns:
  200 {"status": "ok" | "degraded", "checks": {...}, "version": "..."}

The UI HealthBadge polls this every 10 s to show backend connectivity.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db.session import get_db

router = APIRouter()
_settings = get_settings()


@router.get("")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    checks: dict[str, str] = {"api": "ok", "db": "ok", "redis": "ok"}

    # ── DB ────────────────────────────────────────────────────────────────────
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    # ── Redis ──────────────────────────────────────────────────────────────────
    try:
        r = aioredis.from_url(_settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {
        "status": overall,
        "checks": checks,
        "version": _settings.app_version,
        "env": _settings.app_env,
    }
