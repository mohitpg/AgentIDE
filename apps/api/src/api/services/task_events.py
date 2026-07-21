"""
Task-event hub — the bridge between Celery workers and the UI.

Workers publish task progress onto a single Redis channel (see
`workers.progress`). One `TaskEventHub`, started in the FastAPI lifespan, owns the
subscription and does two things for every event:

  1. **Persists** the transition to the `tasks` table (the durable record the
     REST API reads), and
  2. **Fans out** the raw event to every connected `/tasks/ws` client through an
     in-process queue, so the desktop UI updates live.

This makes the gateway the single writer of task *status* transitions: the REST
layer only creates the initial `pending` row and publishes a `revoked` event on
cancel; all forward progress flows through here.

"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.session import AsyncSessionLocal
from api.models.task import TERMINAL_STATUSES, Task

logger = logging.getLogger(__name__)

# Mirrors workers.progress.TASK_EVENTS_CHANNEL.
TASK_EVENTS_CHANNEL = "tasks:events"

# Bound per-subscriber buffering; a client that falls this far behind is dropped.
_SUBSCRIBER_MAXSIZE = 1000


def _now() -> datetime:
    return datetime.now(UTC)


class TaskEventHub:
    """Subscribes to the task-events channel; persists + fans out each event."""

    def __init__(
        self,
        redis_url: str,
        *,
        channel: str = TASK_EVENTS_CHANNEL,
        session_factory: async_sessionmaker = AsyncSessionLocal,
    ) -> None:
        self._redis_url = redis_url
        self._channel = channel
        self._session_factory = session_factory
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._redis: aioredis.Redis | None = None
        self._stopped = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._stopped = False
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    # ── fan-out subscriptions (one per /tasks/ws connection) ───────────────────
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_MAXSIZE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    # ── publishing (used by the /cancel endpoint) ──────────────────────────────
    async def publish(self, event: dict[str, Any]) -> None:
        """Publish an event onto the channel so it flows the same path as workers."""
        if self._redis is None:
            return
        with contextlib.suppress(Exception):
            await self._redis.publish(self._channel, json.dumps(event))

    # ── internals ──────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        assert self._redis is not None
        while not self._stopped:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(self._channel)
                logger.info("task event hub subscribed to %s", self._channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    await self._handle(message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Redis blip — back off and resubscribe.
                logger.warning("task event hub error (%s); reconnecting in 2s", exc)
                await asyncio.sleep(2)

    async def _handle(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("dropping malformed task event")
            return

        try:
            await self._persist(event)
        except Exception:
            logger.exception("failed to persist task event")

        self._fanout(event)

    def _fanout(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the slow consumer's buffer rather than blocking the hub.
                self._subscribers.discard(q)

    async def _persist(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        raw_id = data.get("task_id")
        if not raw_id:
            return
        try:
            task_id = uuid.UUID(str(raw_id))
        except ValueError:
            return

        async with self._session_factory() as db:
            row = await db.get(Task, task_id)
            if row is None:
                return
            # First terminal status wins — never downgrade a finished task.
            if row.status in TERMINAL_STATUSES:
                return

            status = data.get("status")
            if status:
                row.status = status
            if "progress" in data:
                row.progress = int(data["progress"])
            if data.get("message"):
                row.message = data["message"]

            if status in ("started", "progress") and row.started_at is None:
                row.started_at = _now()
            if status in TERMINAL_STATUSES:
                row.finished_at = _now()
                if status == "success":
                    row.result = data.get("result")
                    row.progress = 100
                elif status == "failure":
                    row.error = data.get("error") or data.get("message")

            await db.commit()
