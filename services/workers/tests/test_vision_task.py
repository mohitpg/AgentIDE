"""
analyze_image task: publishes started → progress → success and returns the
analysis result; the staged image is cleaned up. The actual multimodal call
(`_run_async` → VisionAgent) is patched out, so no model/network is needed, and
Redis publishing is patched too — `.apply()` runs the task eagerly in-process.
"""

from __future__ import annotations

from pathlib import Path

import workers.tasks.vision as vis_mod
from workers.tasks.vision import analyze_image


def _capture_publish(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(
        vis_mod, "publish", lambda task_id, **kw: events.append({"task_id": task_id, **kw})
    )
    return events


def test_analyze_image_success_and_cleanup(monkeypatch, tmp_path):
    events = _capture_publish(monkeypatch)

    staged = tmp_path / "shot.png"
    staged.write_bytes(b"\x89PNG\r\n\x1a\n fake")

    fake_result = {"skill": "ocr", "analysis": "hello", "chars": 5}
    monkeypatch.setattr(vis_mod, "_run_async", lambda coro: fake_result)
    # _analyze would build a real coroutine; replace it so nothing is awaited.
    monkeypatch.setattr(vis_mod, "_analyze", lambda *a, **k: None)

    result = analyze_image.apply(
        kwargs={"image_path": str(staged), "prompt": "read it", "skill_id": "ocr"}
    )

    assert result.successful()
    assert result.result == fake_result

    statuses = [e["status"] for e in events]
    assert statuses[0] == "started"
    assert statuses[-1] == "success"
    assert events[-1]["progress"] == 100
    assert events[-1]["result"]["chars"] == 5

    # The staged upload is removed after a successful analysis.
    assert not staged.exists()


def test_analyze_image_missing_file_fails(monkeypatch):
    events = _capture_publish(monkeypatch)
    missing = str(Path("does-not-exist-image-xyz.png"))

    result = analyze_image.apply(kwargs={"image_path": missing})

    assert result.failed()
    assert [e["status"] for e in events] == ["started", "failure"]
    assert events[-1]["error"]
