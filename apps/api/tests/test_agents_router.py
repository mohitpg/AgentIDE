"""
/agents router: register, heartbeat, deregister, list, discover.

The registry is replaced with an in-memory fake via dependency override, so these
exercise the HTTP layer (status codes, payload shaping) without Postgres/Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.main import app
from api.routers.agents import get_registry
from httpx import ASGITransport, AsyncClient


class FakeRegistry:
    """Implements the slice of AgentRegistry the router calls."""

    def __init__(self) -> None:
        self.agents: dict[str, dict] = {}

    def _record(self, agent_id: str, card: dict, url: str | None) -> dict:
        return {
            "agent_id": agent_id,
            "name": card.get("name", agent_id),
            "description": card.get("description"),
            "url": url or card.get("url", ""),
            "version": card.get("version", "0.1.0"),
            "card": card,
            "skill_ids": [s["id"] for s in card.get("skills", [])],
            "tags": sorted({t for s in card.get("skills", []) for t in s.get("tags", [])}),
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }

    async def register(self, agent_id, card, *, url=None):
        rec = self._record(agent_id, card, url)
        self.agents[agent_id] = rec
        return rec

    async def heartbeat(self, agent_id):
        return agent_id in self.agents

    async def deregister(self, agent_id):
        return self.agents.pop(agent_id, None) is not None

    async def list_agents(self, *, include_stale=False):
        return list(self.agents.values())

    async def discover(self, *, skill=None, tag=None):
        out = list(self.agents.values())
        if skill is not None:
            out = [a for a in out if skill in a["skill_ids"]]
        if tag is not None:
            out = [a for a in out if tag in a["tags"]]
        return out


@pytest.fixture
def fake_registry() -> FakeRegistry:
    registry = FakeRegistry()
    app.dependency_overrides[get_registry] = lambda: registry
    yield registry
    app.dependency_overrides.pop(get_registry, None)


@pytest.fixture
async def client(fake_registry, auth):
    # Agents call these endpoints with the shared service token.
    auth.as_service()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


def _card() -> dict:
    return {
        "name": "Code",
        "description": "code agent",
        "url": "http://localhost:8102",
        "version": "0.1.0",
        "skills": [{"id": "generate", "name": "Generate", "tags": ["code"]}],
    }


async def test_register_returns_agent_info(client):
    resp = await client.post(
        "/agents/register", json={"agent_id": "code", "card": _card()}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "code"
    assert data["url"] == "http://localhost:8102"
    assert data["skills"] == ["generate"]


async def test_list_reflects_registrations(client):
    await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    resp = await client.get("/agents")
    assert resp.status_code == 200
    assert [a["agent_id"] for a in resp.json()] == ["code"]


async def test_heartbeat_known_and_unknown(client):
    await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    assert (await client.post("/agents/code/heartbeat")).status_code == 204
    assert (await client.post("/agents/ghost/heartbeat")).status_code == 404


async def test_deregister(client):
    await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    assert (await client.delete("/agents/code")).status_code == 204
    assert (await client.delete("/agents/code")).status_code == 404
    assert (await client.get("/agents")).json() == []


async def test_unauthenticated_is_rejected(client, auth):
    auth.clear()
    assert (await client.get("/agents")).status_code == 401
    resp = await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    assert resp.status_code == 401


async def test_users_can_list_but_not_register(client, auth):
    auth.as_user(auth.make_user())
    assert (await client.get("/agents")).status_code == 200
    resp = await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    assert resp.status_code == 403


async def test_discover_by_skill(client):
    await client.post("/agents/register", json={"agent_id": "code", "card": _card()})
    hit = await client.get("/agents/discover", params={"skill": "generate"})
    assert [a["agent_id"] for a in hit.json()] == ["code"]
    miss = await client.get("/agents/discover", params={"skill": "nope"})
    assert miss.json() == []
