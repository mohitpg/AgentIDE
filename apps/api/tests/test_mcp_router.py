"""
/mcp router: register, list, assign-to-agent, update, delete, live discover.

The store is replaced with an in-memory fake via dependency override (like the
agents router test), so these exercise the HTTP layer without Postgres. The
discover endpoint's `connect_server` is monkeypatched so no subprocess spawns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api.main import app
from api.routers.mcp import get_store
from api.schemas.mcp import MCPServerCreate, MCPServerUpdate
from httpx import ASGITransport, AsyncClient


def _row(**overrides) -> SimpleNamespace:
    base = {
        "id": "git",
        "name": "Git",
        "description": "git ops",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-git"],
        "env": {},
        "cwd": None,
        "url": "",
        "enabled": True,
        "allowed_agents": [],
        "tool_allowlist": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    async def list(self):
        return [self.rows[k] for k in sorted(self.rows)]

    async def get(self, server_id):
        return self.rows.get(server_id)

    async def create(self, data: MCPServerCreate):
        row = _row(**data.model_dump())
        self.rows[data.id] = row
        return row

    async def update(self, server_id, data: MCPServerUpdate):
        row = self.rows.get(server_id)
        if row is None:
            return None
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        return row

    async def delete(self, server_id):
        return self.rows.pop(server_id, None) is not None


@pytest.fixture
def store() -> FakeStore:
    fake = FakeStore()
    app.dependency_overrides[get_store] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_store, None)


@pytest.fixture
async def client(store, auth):
    # MCP servers are host-wide; changing them is admin-only.
    auth.as_user(auth.make_user(admin=True))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


def _payload(**overrides) -> dict:
    body = {
        "id": "git",
        "name": "Git",
        "command": "uvx",
        "args": ["mcp-server-git"],
        "allowed_agents": ["code"],
    }
    body.update(overrides)
    return body


async def test_create_then_get(client):
    resp = await client.post("/mcp", json=_payload())
    assert resp.status_code == 201
    assert resp.json()["id"] == "git"

    got = await client.get("/mcp/git")
    assert got.status_code == 200
    assert got.json()["allowed_agents"] == ["code"]


async def test_create_duplicate_conflicts(client):
    await client.post("/mcp", json=_payload())
    dup = await client.post("/mcp", json=_payload())
    assert dup.status_code == 409


async def test_create_rejects_bad_id(client):
    bad = await client.post("/mcp", json=_payload(id="has spaces"))
    assert bad.status_code == 422


async def test_list_servers(client):
    await client.post("/mcp", json=_payload(id="git"))
    await client.post("/mcp", json=_payload(id="filesystem", allowed_agents=[]))
    resp = await client.get("/mcp")
    assert [s["id"] for s in resp.json()] == ["filesystem", "git"]


async def test_servers_for_agent_filters_by_assignment_and_enabled(client, auth):
    await client.post("/mcp", json=_payload(id="git", allowed_agents=["code"]))
    await client.post("/mcp", json=_payload(id="fetch", allowed_agents=["web"]))
    await client.post("/mcp", json=_payload(id="fs", allowed_agents=[]))  # all agents
    await client.post("/mcp", json=_payload(id="off", allowed_agents=["code"], enabled=False))

    auth.as_service()  # agents fetch their assignments with the service token
    resp = await client.get("/mcp/agent/code")
    assert {s["id"] for s in resp.json()} == {"git", "fs"}  # not fetch (web), not off


async def test_non_admin_can_read_but_not_change(client, auth):
    await client.post("/mcp", json=_payload())
    auth.as_user(auth.make_user(admin=False))

    assert (await client.get("/mcp")).status_code == 200
    assert (await client.get("/mcp/git")).status_code == 200
    assert (await client.post("/mcp", json=_payload(id="x"))).status_code == 403
    assert (await client.patch("/mcp/git", json={"enabled": False})).status_code == 403
    assert (await client.delete("/mcp/git")).status_code == 403
    assert (await client.post("/mcp/git/discover")).status_code == 403


async def test_agent_assignments_are_service_only(client):
    # An admin user is still not an agent: the assignments carry MCP env secrets.
    assert (await client.get("/mcp/agent/code")).status_code == 403


async def test_update_toggles_enabled(client):
    await client.post("/mcp", json=_payload())
    resp = await client.patch("/mcp/git", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_update_missing_404(client):
    resp = await client.patch("/mcp/ghost", json={"enabled": False})
    assert resp.status_code == 404


async def test_delete(client):
    await client.post("/mcp", json=_payload())
    assert (await client.delete("/mcp/git")).status_code == 204
    assert (await client.delete("/mcp/git")).status_code == 404


async def test_builtins_template_list(client):
    resp = await client.get("/mcp/builtins")
    assert resp.status_code == 200
    assert {s["id"] for s in resp.json()} == {"filesystem", "git", "fetch"}


async def test_discover_lists_tools(client, monkeypatch):
    await client.post("/mcp", json=_payload())

    class FakeConn:
        async def list_tools(self):
            return [
                SimpleNamespace(name="status", description="git status", input_schema={})
            ]

        async def aclose(self):
            pass

    async def fake_connect(config):
        return FakeConn()

    monkeypatch.setattr("api.routers.mcp.connect_server", fake_connect)
    resp = await client.post("/mcp/git/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert [t["name"] for t in body["tools"]] == ["status"]


async def test_discover_reports_connection_failure(client, monkeypatch):
    await client.post("/mcp", json=_payload())

    async def fake_connect(config):
        raise RuntimeError("uvx not found")

    monkeypatch.setattr("api.routers.mcp.connect_server", fake_connect)
    resp = await client.post("/mcp/git/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert "uvx not found" in body["error"]
