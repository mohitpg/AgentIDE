"""
/tasks router: enqueue, list, get, cancel.

Exercises the HTTP layer against in-memory SQLite (get_db overridden) with the
Celery dispatch/revoke calls patched out and a fake task-event hub on app.state —
so no Postgres, Redis, or broker is required.
"""

from __future__ import annotations

import pytest
from api.db.session import get_db
from api.main import app
from api.models.task import Task
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class FakeHub:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.published.append(event)


@pytest.fixture
async def sqlite_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Task.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def calls(monkeypatch) -> dict:
    """Patch the router's dispatch/revoke; record invocations."""
    recorded: dict = {"enqueue": [], "revoke": []}
    monkeypatch.setattr(
        "api.routers.tasks.enqueue",
        lambda name, **kw: recorded["enqueue"].append({"name": name, **kw}),
    )
    monkeypatch.setattr(
        "api.routers.tasks.revoke",
        lambda task_id: recorded["revoke"].append(task_id),
    )
    return recorded


@pytest.fixture
async def client(sqlite_factory, calls, auth):
    auth.as_user(auth.make_user())

    async def override_db():
        async with sqlite_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    hub = FakeHub()
    app.state.task_hub = hub
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        c.fake_hub = hub  # type: ignore[attr-defined]
        c.calls = calls  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.pop(get_db, None)
    if hasattr(app.state, "task_hub"):
        delattr(app.state, "task_hub")


async def test_create_enqueues_mock_task(client):
    resp = await client.post("/tasks", json={"type": "mock"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "mock"
    assert data["status"] == "pending"
    assert data["queue"] == "default"

    # Dispatched by name, with the row id as the Celery task id.
    [call] = client.calls["enqueue"]
    assert call["name"] == "workers.tasks.mock.long_task"
    assert call["task_id"] == data["id"]
    assert call["queue"] == "default"
    assert call["kwargs"]["steps"] == 12  # catalog default applied


async def test_create_merges_params_over_defaults(client):
    resp = await client.post(
        "/tasks", json={"type": "mock", "label": "custom", "params": {"steps": 3}}
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "custom"
    [call] = client.calls["enqueue"]
    assert call["kwargs"]["steps"] == 3  # override
    assert call["kwargs"]["delay"] == 0.8  # default retained


async def test_create_unknown_type_400(client):
    resp = await client.post("/tasks", json={"type": "nope"})
    assert resp.status_code == 400
    assert client.calls["enqueue"] == []


async def test_list_and_get(client):
    created = (await client.post("/tasks", json={"type": "mock"})).json()

    listing = await client.get("/tasks")
    assert listing.status_code == 200
    assert [t["id"] for t in listing.json()] == [created["id"]]

    one = await client.get(f"/tasks/{created['id']}")
    assert one.status_code == 200
    assert one.json()["id"] == created["id"]

    missing = await client.get("/tasks/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


async def test_cancel_revokes_and_publishes(client):
    created = (await client.post("/tasks", json={"type": "mock"})).json()

    resp = await client.post(f"/tasks/{created['id']}/cancel")
    assert resp.status_code == 202
    assert client.calls["revoke"] == [created["id"]]

    [event] = client.fake_hub.published
    assert event["type"] == "task_progress"
    assert event["data"]["status"] == "revoked"
    assert event["data"]["task_id"] == created["id"]


async def test_tasks_are_invisible_outside_their_scope(client, auth):
    owner = auth.make_user()
    scope = auth.as_user(owner)
    created = (await client.post("/tasks", json={"type": "mock"})).json()
    tid = created["id"]

    # Another user — and the same user in another project — see nothing.
    for other in (auth.make_user(), owner):
        auth.as_user(other)  # fresh random project each time
        assert (await client.get("/tasks")).json() == []
        assert (await client.get(f"/tasks/{tid}")).status_code == 404
        assert (await client.post(f"/tasks/{tid}/cancel")).status_code == 404
    assert client.calls["revoke"] == []

    auth.as_user(owner, scope.project_id)
    assert [t["id"] for t in (await client.get("/tasks")).json()] == [tid]


async def test_unauthenticated_is_rejected(client, auth):
    auth.clear()
    assert (await client.get("/tasks")).status_code == 401
    assert (await client.post("/tasks", json={"type": "mock"})).status_code == 401


async def test_cancel_unknown_404(client):
    resp = await client.post("/tasks/00000000-0000-0000-0000-000000000000/cancel")
    assert resp.status_code == 404
