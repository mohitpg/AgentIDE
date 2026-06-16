"""
/tasks – async task API (Phase 3).

Long-running work runs off the request path on the Celery workers; this router is
the gateway to it:

  POST   /tasks                enqueue a task (returns the pending record)
  GET    /tasks                list tasks (newest first, ?status= / ?session_id=)
  GET    /tasks/{task_id}      fetch one task record
  POST   /tasks/{task_id}/cancel   request cancellation of a running task
  WS     /tasks/ws             live task progress (unified StreamEvents)

Task *records* live in Postgres; live *progress* arrives over the WebSocket, fed
by the `TaskEventHub` from worker-published Redis pub/sub. The two are independent
of the chat WebSocket, so async tasks never block (or are blocked by) chat.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.events import TaskProgressEvent

from api.db.session import get_db
from api.models.task import Task
from api.schemas.tasks import TaskCreate, TaskRead
from api.services.celery_client import enqueue, revoke

logger = logging.getLogger(__name__)
router = APIRouter()

# Logical task type → (Celery task name, queue, default kwargs, default label).
# Phase 3 exposes only the mock task; Phases 4-5 add indexing/vision/web here.
TASK_CATALOG: dict[str, dict] = {
    "mock": {
        "name": "workers.tasks.mock.long_task",
        "queue": "default",
        "label": "Mock long task",
        "defaults": {"steps": 12, "delay": 0.8},
    },
}


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
) -> Task:
    spec = TASK_CATALOG.get(payload.type)
    if spec is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown task type '{payload.type}'. Known: {sorted(TASK_CATALOG)}",
        )

    task = Task(
        type=payload.type,
        label=payload.label or spec["label"],
        queue=spec["queue"],
        status="pending",
        params=payload.params,
        session_id=payload.session_id,
    )
    db.add(task)
    # Commit before dispatch so the worker's first progress event always finds the
    # row (the event listener reads it from a separate session).
    await db.commit()
    await db.refresh(task)

    kwargs = {**spec.get("defaults", {}), **payload.params}
    try:
        await asyncio.to_thread(
            enqueue, spec["name"], task_id=str(task.id), queue=spec["queue"], kwargs=kwargs
        )
    except Exception as exc:  # broker down — reflect the dispatch failure on the row
        logger.exception("failed to enqueue task %s", task.id)
        task.status = "failure"
        task.error = f"dispatch failed: {exc}"
        await db.commit()
        await db.refresh(task)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to enqueue task") from exc

    return task


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    status_filter: str | None = Query(None, alias="status"),
    session_id: uuid.UUID | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Task.status == status_filter)
    if session_id is not None:
        stmt = stmt.where(Task.session_id == session_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if task.is_terminal:
        return {"status": task.status, "detail": "Task already finished"}

    await asyncio.to_thread(revoke, str(task_id))

    # Publish a revoked event so the hub persists it + notifies WS clients,
    # keeping the single-writer invariant (status transitions flow through the hub).
    hub = getattr(request.app.state, "task_hub", None)
    if hub is not None:
        event = TaskProgressEvent(
            run_id=str(task_id),
            data={
                "task_id": str(task_id),
                "status": "revoked",
                "progress": task.progress,
                "message": "Canceled by user",
            },
        )
        await hub.publish(event.model_dump(mode="json"))

    return {"status": "revoking"}


@router.websocket("/ws")
async def tasks_ws(websocket: WebSocket) -> None:
    """Stream every task progress event to the client as it arrives."""
    await websocket.accept()
    hub = getattr(websocket.app.state, "task_hub", None)
    if hub is None:
        await websocket.close(code=1011)
        return

    queue = hub.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # send failed (client gone) — clean up below
        logger.debug("tasks ws send failed; closing", exc_info=True)
    finally:
        hub.unsubscribe(queue)
