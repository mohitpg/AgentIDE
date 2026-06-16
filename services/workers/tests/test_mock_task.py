"""
Mock long task: it should publish a started → progress* → success sequence and
return its result. Redis publishing is patched out, so no broker/Redis is needed;
`.apply()` runs the task eagerly in-process.
"""

from __future__ import annotations

import workers.tasks.mock as mock_mod
from workers.tasks.mock import long_task


def test_long_task_publishes_lifecycle_and_returns_result(monkeypatch):
    events: list[dict] = []

    def fake_publish(task_id, **kwargs):
        events.append({"task_id": task_id, **kwargs})

    monkeypatch.setattr(mock_mod, "publish", fake_publish)

    result = long_task.apply(kwargs={"steps": 3, "delay": 0, "label": "unit"})

    assert result.successful()
    assert result.result == {"label": "unit", "steps": 3, "completed": True}

    statuses = [e["status"] for e in events]
    assert statuses[0] == "started"
    assert statuses.count("progress") == 3
    assert statuses[-1] == "success"

    # Progress climbs monotonically and ends at 100.
    progresses = [e["progress"] for e in events if e["status"] == "progress"]
    assert progresses == sorted(progresses)
    assert progresses[-1] == 100

    success = events[-1]
    assert success["result"]["completed"] is True

    # Every event carries the same task id (== the Celery request id).
    assert len({e["task_id"] for e in events}) == 1


def test_long_task_publishes_failure_then_reraises(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        mock_mod, "publish", lambda task_id, **kw: events.append({"task_id": task_id, **kw})
    )

    # Make the work blow up after "started" is published.
    def boom(_seconds):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mock_mod.time, "sleep", boom)

    result = long_task.apply(kwargs={"steps": 2, "delay": 1})

    assert result.failed()
    assert [e["status"] for e in events] == ["started", "failure"]
    assert events[-1]["error"] == "kaboom"
