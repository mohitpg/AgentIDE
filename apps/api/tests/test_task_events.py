"""
TaskEventHub persistence: progress events drive the task row, the first terminal
status wins, and unknown / malformed events are ignored.

Runs against in-memory SQLite (the Task model uses portable column types) with the
session factory injected — no Redis or Postgres needed.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.task import Task
from api.services.task_events import TaskEventHub
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def session_factory():
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
def hub(session_factory) -> TaskEventHub:
    return TaskEventHub("redis://unused", session_factory=session_factory)


async def _make_task(factory, **kwargs) -> uuid.UUID:
    task = Task(type="mock", label="t", queue="default", status="pending", **kwargs)
    async with factory() as db:
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


def _event(task_id, **data) -> dict:
    return {"type": "task_progress", "run_id": str(task_id), "data": {"task_id": str(task_id), **data}}


async def test_progress_updates_row(hub, session_factory):
    tid = await _make_task(session_factory)

    await hub._persist(_event(tid, status="started", progress=0, message="go"))
    await hub._persist(_event(tid, status="progress", progress=40, message="step"))

    async with session_factory() as db:
        row = await db.get(Task, tid)
    assert row.status == "progress"
    assert row.progress == 40
    assert row.message == "step"
    assert row.started_at is not None


async def test_success_sets_result_and_finishes(hub, session_factory):
    tid = await _make_task(session_factory)
    await hub._persist(
        _event(tid, status="success", message="done", result={"completed": True})
    )

    async with session_factory() as db:
        row = await db.get(Task, tid)
    assert row.status == "success"
    assert row.progress == 100
    assert row.result == {"completed": True}
    assert row.finished_at is not None


async def test_first_terminal_status_wins(hub, session_factory):
    tid = await _make_task(session_factory)
    await hub._persist(_event(tid, status="success", result={"ok": True}))
    # A late revoke must not clobber an already-finished task.
    await hub._persist(_event(tid, status="revoked", message="too late"))

    async with session_factory() as db:
        row = await db.get(Task, tid)
    assert row.status == "success"
    assert row.message != "too late"


async def test_unknown_task_id_is_ignored(hub, session_factory):
    # No row exists; persisting must be a no-op (and not raise).
    await hub._persist(_event(uuid.uuid4(), status="progress", progress=10))


async def test_events_fan_out_only_to_the_tasks_owner(hub, session_factory):
    import json

    alice, alice_proj, bob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tid = await _make_task(session_factory, user_id=alice, project_id=alice_proj)

    mine = hub.subscribe(user_id=alice, project_id=alice_proj)
    other_project = hub.subscribe(user_id=alice, project_id=uuid.uuid4())
    other_user = hub.subscribe(user_id=bob, project_id=alice_proj)

    await hub._handle(json.dumps(_event(tid, status="progress", progress=10)))
    # Unknown task: no owner to route to, so nobody gets it.
    await hub._handle(json.dumps(_event(uuid.uuid4(), status="progress", progress=10)))

    assert mine.qsize() == 1
    assert other_project.empty()
    assert other_user.empty()


async def test_malformed_event_is_dropped(hub):
    # _handle tolerates non-JSON without raising.
    await hub._handle("not json {")
