"""
Shared async Redis client.

A single connection pool for the process, mirroring `db/session.py`. Used by the
agent registry cache (and future caches). `decode_responses=True` so callers get
`str` back and can `json.loads` directly.

The client is created lazily at import; connections are only opened on first use,
so importing this module never requires Redis to be up.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from api.config import get_settings

_settings = get_settings()

redis_client: aioredis.Redis = aioredis.from_url(
    _settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency – yields the shared Redis client."""
    return redis_client
