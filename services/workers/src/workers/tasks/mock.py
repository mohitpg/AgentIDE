"""
Mock long-running task.

`long_task` does nothing useful: it sleeps in steps and reports progress. It
exists to prove the async path end-to-end (enqueue → worker → Redis pub/sub →
API persists + streams → UI shows live progress and the final result) without
needing any real workload. 

The Celery task id is set by the gateway when it enqueues (`task_id=<row id>`),
so `self.request.id` is exactly the `tasks` row id — every progress event lines
up with its database record with no extra correlation.
"""

from __future__ import annotations

import time

from workers.app import celery_app
from workers.progress import publish


@celery_app.task(name="workers.tasks.mock.long_task", bind=True)
def long_task(  # type: ignore[no-untyped-def]
    self,
    steps: int = 12,
    delay: float = 0.8,
    label: str = "Mock long task",
):
    """Sleep `steps` times, publishing progress after each step."""
    task_id = self.request.id
    steps = max(1, int(steps))
    delay = max(0.0, float(delay))

    publish(task_id, status="started", progress=0, message=f"Starting: {label}")
    try:
        for i in range(1, steps + 1):
            time.sleep(delay)
            pct = int(i / steps * 100)
            publish(
                task_id,
                status="progress",
                progress=pct,
                message=f"Step {i}/{steps}",
            )
            # Mirror into Celery's own result backend for out-of-band inspection.
            self.update_state(state="PROGRESS", meta={"progress": pct})

        result = {"label": label, "steps": steps, "completed": True}
        publish(task_id, status="success", progress=100, message="Completed", result=result)
        return result
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI then re-raise
        publish(task_id, status="failure", message=str(exc), error=str(exc))
        raise
