"""
index_document task: publishes started → progress* → success and returns the
ingest result; the staged file is cleaned up. The actual pgvector ingest is
patched out (``_run_async``), so no database/models are needed, and Redis
publishing is patched too — `.apply()` runs the task eagerly in-process.
"""

from __future__ import annotations

from pathlib import Path

import workers.tasks.indexing as idx_mod
from workers.tasks.indexing import index_document


def _capture_publish(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(
        idx_mod, "publish", lambda task_id, **kw: events.append({"task_id": task_id, **kw})
    )
    return events


def test_index_document_success_and_cleanup(monkeypatch, tmp_path):
    events = _capture_publish(monkeypatch)

    staged = tmp_path / "doc.txt"
    staged.write_text("hello world", encoding="utf-8")

    fake_result = {"document_id": "11111111-1111-1111-1111-111111111111",
                   "filename": "doc.txt", "num_chunks": 3}
    monkeypatch.setattr(idx_mod, "_run_async", lambda coro: fake_result)
    # _ingest_async would build a real coroutine; replace it so nothing is awaited.
    monkeypatch.setattr(idx_mod, "_ingest_async", lambda *a, **k: None)

    result = index_document.apply(kwargs={
        "document_path": str(staged),
        "filename": "doc.txt",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "project_id": "33333333-3333-3333-3333-333333333333",
    })

    assert result.successful()
    assert result.result == fake_result

    statuses = [e["status"] for e in events]
    assert statuses[0] == "started"
    assert statuses[-1] == "success"
    assert events[-1]["progress"] == 100
    assert events[-1]["result"]["num_chunks"] == 3

    # The staged upload is removed after a successful index.
    assert not staged.exists()


def test_index_document_without_owner_fails_and_cleans_up(monkeypatch, tmp_path):
    events = _capture_publish(monkeypatch)
    staged = tmp_path / "doc.txt"
    staged.write_text("hello world", encoding="utf-8")
    monkeypatch.setattr(idx_mod, "_run_async", lambda coro: {"num_chunks": 1})

    result = index_document.apply(kwargs={"document_path": str(staged)})

    # An ownerless document would be unsearchable, so it's refused outright.
    assert result.failed()
    assert events[-1]["status"] == "failure"
    assert "user_id" in events[-1]["error"]
    assert not staged.exists()


def test_index_document_missing_file_fails(monkeypatch):
    events = _capture_publish(monkeypatch)
    missing = str(Path("does-not-exist-xyz.txt"))

    result = index_document.apply(kwargs={"document_path": missing})

    assert result.failed()
    assert [e["status"] for e in events] == ["started", "failure"]
    assert events[-1]["error"]
