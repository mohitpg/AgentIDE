"""
Gateway-side Celery client (Phase 3).

The API enqueues and cancels jobs but never runs them, so it builds its own
lightweight Celery app that shares the broker/backend and the queue topology with
`services/workers`, and dispatches by **task name** (`send_task`) rather than
importing the worker task code. This keeps the gateway decoupled from the worker
package — they only agree on broker config + task names.

Both calls hit RabbitMQ synchronously, so callers should run them off the event
loop (``await asyncio.to_thread(...)``).
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from kombu import Exchange, Queue

from api.config import get_settings

_settings = get_settings()
_exchange = Exchange("agentide", type="direct")

# Same broker, backend, exchange and queues as services/workers/app.py so messages
# route to the queues the workers consume.
celery_client = Celery(
    "agentide-gateway",
    broker=_settings.rabbitmq_url,
    backend=_settings.redis_url,
)
celery_client.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_queues=(
        Queue("default", _exchange, routing_key="default"),
        Queue("indexing", _exchange, routing_key="indexing"),
        Queue("vision", _exchange, routing_key="vision"),
        Queue("web", _exchange, routing_key="web"),
    ),
    task_default_queue="default",
    task_default_exchange="agentide",
    task_default_routing_key="default",
)


def enqueue(
    name: str,
    *,
    task_id: str,
    queue: str = "default",
    kwargs: dict[str, Any] | None = None,
) -> None:
    """Dispatch a task by name with a caller-chosen id (blocking broker publish)."""
    celery_client.send_task(
        name,
        task_id=task_id,
        queue=queue,
        kwargs=kwargs or {},
    )


def revoke(task_id: str) -> None:
    """Request termination of a running/queued task (blocking control broadcast)."""
    celery_client.control.revoke(task_id, terminate=True, signal="SIGTERM")
