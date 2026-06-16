"""
Task progress publishing — Celery worker → Redis pub/sub → API → UI.

A running task publishes its lifecycle as the shared `TaskProgressEvent` onto a
single Redis channel. The API gateway subscribes to that channel, persists each
update to the `tasks` table, and fans it out to connected `/tasks/ws` clients
(see `api.services.task_events.TaskEventHub`). Because the event is the same
unified `StreamEvent` the chat path uses, the desktop UI renders it with the same
machinery.

The worker stays deliberately thin: it never touches Postgres. Redis is the only
side-channel it needs, and every publish degrades to a logged warning if Redis is
unavailable — a missed progress frame must never fail the task itself.

The channel name is a constant shared (by convention) with the API listener; keep
the two in sync.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import redis
from schemas.events import TaskProgressEvent

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Single channel all task events flow over. Mirrored in api.services.task_events.
TASK_EVENTS_CHANNEL = "tasks:events"

# Lifecycle statuses carried in event.data["status"].
#   pending  – row created, queued (set by the API, not the worker)
#   started  – worker picked the task up
#   progress – running, with a 0-100 percentage
#   success  – finished, `result` present
#   failure  – raised, `error` present
#   revoked  – cancelled (set by the API on /cancel)

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    """Lazily build a process-wide sync Redis client (one per worker process)."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def publish(
    task_id: str,
    *,
    status: str,
    progress: int | None = None,
    message: str = "",
    result: Any | None = None,
    error: str | None = None,
) -> None:
    """
    Publish one task lifecycle event. Best-effort: failures are logged, not raised,
    so progress reporting never breaks the work itself.
    """
    data: dict[str, Any] = {"task_id": task_id, "status": status, "message": message}
    if progress is not None:
        data["progress"] = progress
    if result is not None:
        data["result"] = result
    if error is not None:
        data["error"] = error

    # run_id == task_id so task events correlate the same way chat runs do.
    event = TaskProgressEvent(run_id=task_id, data=data)
    try:
        _redis().publish(TASK_EVENTS_CHANNEL, event.model_dump_json())
    except Exception as exc:  # Redis down — the task continues regardless.
        logger.warning("task progress publish failed for %s (%s)", task_id, exc)
