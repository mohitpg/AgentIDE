"""
AgentRegistry: register / heartbeat / deregister, liveness, discovery, caching.

Runs against an in-memory SQLite DB (the model uses portable JSON columns) and a
tiny in-process fake for Redis, so no real infrastructure is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api.models.agent import AgentRegistration
from api.services import registry as registry_mod
from api.services.registry import AgentRegistry, skill_ids, tags
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FakeRedis:
    """Minimal async Redis stand-in: get / set / delete with TTL ignored."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _card(agent_id: str = "code", *, url: str = "http://localhost:8102") -> dict:
    return {
        "name": agent_id.capitalize(),
        "description": f"{agent_id} agent",
        "url": url,
        "version": "0.1.0",
        "skills": [
            {"id": "generate", "name": "Generate", "tags": ["code", "write"]},
            {"id": "explain", "name": "Explain", "tags": ["code"]},
        ],
    }


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AgentRegistration.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def registry(db, redis) -> AgentRegistry:
    return AgentRegistry(db, redis, ttl_seconds=90, cache_ttl_seconds=30)


# ── card helpers ───────────────────────────────────────────────────────────────


def test_skill_ids_and_tags_extracted_from_card():
    card = _card()
    assert skill_ids(card) == ["generate", "explain"]
    assert tags(card) == ["code", "write"]  # union, de-duped, order preserved


# ── register / list / liveness ─────────────────────────────────────────────────


async def test_register_then_list(registry):
    rec = await registry.register("code", _card())
    assert rec["agent_id"] == "code"
    assert rec["url"] == "http://localhost:8102"

    agents = await registry.list_agents()
    assert [a["agent_id"] for a in agents] == ["code"]
    assert agents[0]["skill_ids"] == ["generate", "explain"]


async def test_register_is_idempotent_upsert(registry):
    await registry.register("code", _card())
    await registry.register("code", _card(url="http://newhost:9000"))
    agents = await registry.list_agents()
    assert len(agents) == 1
    assert agents[0]["url"] == "http://newhost:9000"


async def test_url_override_wins_over_card(registry):
    rec = await registry.register("code", _card(), url="http://gateway-visible:8102")
    assert rec["url"] == "http://gateway-visible:8102"


async def test_stale_agents_filtered_unless_requested(registry, db):
    await registry.register("code", _card())
    # Age the heartbeat past the TTL window directly in the row.
    row = await db.get(AgentRegistration, "code")
    row.last_heartbeat = datetime.now(UTC) - timedelta(seconds=120)
    await db.flush()
    await registry._invalidate()  # force a fresh read from the DB

    assert await registry.list_agents() == []
    stale = await registry.list_agents(include_stale=True)
    assert [a["agent_id"] for a in stale] == ["code"]


# ── heartbeat ──────────────────────────────────────────────────────────────────


async def test_heartbeat_revives_a_stale_agent(registry, db):
    await registry.register("code", _card())
    row = await db.get(AgentRegistration, "code")
    row.last_heartbeat = datetime.now(UTC) - timedelta(seconds=120)
    await db.flush()
    await registry._invalidate()
    assert await registry.list_agents() == []

    assert await registry.heartbeat("code") is True
    assert [a["agent_id"] for a in await registry.list_agents()] == ["code"]


async def test_heartbeat_unknown_agent_returns_false(registry):
    assert await registry.heartbeat("ghost") is False


# ── deregister ─────────────────────────────────────────────────────────────────


async def test_deregister_removes_agent(registry):
    await registry.register("code", _card())
    assert await registry.deregister("code") is True
    assert await registry.list_agents() == []


async def test_deregister_unknown_agent_returns_false(registry):
    assert await registry.deregister("ghost") is False


# ── discovery ──────────────────────────────────────────────────────────────────


async def test_discover_by_skill_and_tag(registry):
    await registry.register("code", _card("code"))
    await registry.register(
        "summarizer",
        {
            "name": "Summarizer",
            "url": "http://localhost:8101",
            "version": "0.1.0",
            "skills": [{"id": "tl;dr", "name": "TLDR", "tags": ["text"]}],
        },
    )

    by_skill = await registry.discover(skill="generate")
    assert [a["agent_id"] for a in by_skill] == ["code"]

    by_tag = await registry.discover(tag="text")
    assert [a["agent_id"] for a in by_tag] == ["summarizer"]

    assert await registry.discover(skill="does-not-exist") == []


async def test_url_map_for_supervisor(registry):
    await registry.register("code", _card("code"))
    assert await registry.url_map() == {"code": "http://localhost:8102"}


async def test_reap_stale(registry, db):
    await registry.register("code", _card())
    row = await db.get(AgentRegistration, "code")
    row.last_heartbeat = datetime.now(UTC) - timedelta(seconds=120)
    await db.flush()

    removed = await registry.reap_stale()
    assert removed == 1
    assert await registry.list_agents(include_stale=True) == []


# ── caching ────────────────────────────────────────────────────────────────────


async def test_list_is_served_from_cache_until_invalidated(registry, redis, db):
    await registry.register("code", _card())
    await registry.list_agents()  # populates the cache
    assert registry_mod._CACHE_KEY in redis.store

    # Mutate the DB behind the cache: the cached snapshot should still be served.
    await db.execute(AgentRegistration.__table__.delete())
    await db.flush()
    assert [a["agent_id"] for a in await registry.list_agents()] == ["code"]

    # After invalidation, the fresh (empty) DB state is read.
    await registry._invalidate()
    assert await registry.list_agents() == []
