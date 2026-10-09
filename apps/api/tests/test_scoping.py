"""
Per-user / per-project isolation for /projects and /sessions.

In-memory SQLite with `get_db` overridden; the `auth` fixture stands in for the
signed-in user. Every check here is "the other tenant gets a 404 / empty list".
"""

from __future__ import annotations

import uuid

import pytest
from api.db.session import get_db
from api.main import app
from api.models import Conversation, Image, Message, Project, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for model in (User, Project, Conversation, Message, Image):
            await conn.run_sync(model.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    async def override_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _conversation(factory, user_id, project_id, title="chat") -> uuid.UUID:
    async with factory() as db:
        conv = Conversation(id=uuid.uuid4(), user_id=user_id, project_id=project_id, title=title)
        db.add(conv)
        db.add(Message(conversation_id=conv.id, role="user", content="secret"))
        await db.commit()
        return conv.id


# ── /projects ─────────────────────────────────────────────────────────────────
async def test_projects_are_per_user(client, auth):
    alice, bob = auth.make_user(), auth.make_user()

    auth.as_user(alice)
    created = await client.post("/projects", json={"name": "Alpha"})
    assert created.status_code == 201
    pid = created.json()["id"]

    auth.as_user(bob)
    assert (await client.get("/projects")).json() == []
    assert (await client.patch(f"/projects/{pid}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/projects/{pid}")).status_code == 404

    auth.as_user(alice)
    assert [p["name"] for p in (await client.get("/projects")).json()] == ["Alpha"]


async def test_cannot_delete_last_project(client, auth):
    auth.as_user(auth.make_user())
    only = (await client.post("/projects", json={"name": "Only"})).json()
    assert (await client.delete(f"/projects/{only['id']}")).status_code == 409

    other = (await client.post("/projects", json={"name": "Second"})).json()
    assert (await client.delete(f"/projects/{other['id']}")).status_code == 204


# ── /sessions ─────────────────────────────────────────────────────────────────
async def test_sessions_are_scoped_to_user_and_project(client, auth, factory):
    alice = auth.make_user()
    scope = auth.as_user(alice)
    conv_id = await _conversation(factory, alice.id, scope.project_id)

    assert [s["id"] for s in (await client.get("/sessions")).json()] == [str(conv_id)]
    detail = await client.get(f"/sessions/{conv_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["content"] == "secret"

    # Same user, different project: invisible.
    auth.as_user(alice)
    assert (await client.get("/sessions")).json() == []
    assert (await client.get(f"/sessions/{conv_id}")).status_code == 404

    # Different user: invisible, and can't delete it.
    auth.as_user(auth.make_user(), scope.project_id)
    assert (await client.get("/sessions")).json() == []
    assert (await client.get(f"/sessions/{conv_id}")).status_code == 404
    assert (await client.delete(f"/sessions/{conv_id}")).status_code == 404

    auth.as_user(alice, scope.project_id)
    assert (await client.delete(f"/sessions/{conv_id}")).status_code == 204


async def test_pre_auth_rows_are_invisible(client, auth, factory):
    """Rows with no owner (created before auth) match nobody's scope."""
    await _conversation(factory, None, None)
    auth.as_user(auth.make_user())
    assert (await client.get("/sessions")).json() == []


async def test_missing_project_header_is_rejected(client, auth):
    """Without the get_scope override, a user with no X-Project-Id gets a 400."""
    from api.deps import get_scope

    auth.as_user(auth.make_user())
    app.dependency_overrides.pop(get_scope, None)
    assert (await client.get("/sessions")).status_code == 400
    bad = await client.get("/sessions", headers={"X-Project-Id": "not-a-uuid"})
    assert bad.status_code == 400
    unknown = await client.get("/sessions", headers={"X-Project-Id": str(uuid.uuid4())})
    assert unknown.status_code == 404
