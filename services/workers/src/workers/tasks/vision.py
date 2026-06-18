"""
Vision tasks — large / batch image analysis on the `vision` queue (Phase 5).

The synchronous path is the Vision A2A agent (inline streaming for interactive
turns). This Celery task is the async path for heavy jobs: the gateway stages an
uploaded image on disk, creates a `tasks` row, and enqueues `analyze_image` with
the file path + prompt. The worker reads the bytes, runs the **same** `VisionAgent`
skill the live agent uses (one source of truth for prompts/behaviour), and reports
lifecycle/progress over Redis pub/sub exactly like the indexing task — so the
existing TaskEventHub persists the record and the UI Tasks panel renders live
status with no new plumbing.

The worker stays thin: Redis is the only side-channel it touches for status; every
publish degrades to a logged warning if Redis is down (see `workers.progress`).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any, TypeVar

from workers.app import celery_app
from workers.progress import publish

T = TypeVar("T")


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Drive an async coroutine from the sync Celery task (selector loop on Win)."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


async def _collect(stream: AsyncIterator[str]) -> str:
    return "".join([chunk async for chunk in stream])


async def _analyze(path: Path, prompt: str, skill_id: str) -> dict:
    # Lazy import keeps worker boot light and independent of the agent stack until
    # a vision job actually runs (mirrors how indexing.py imports `rag`).
    from agent_vision import VisionAgent
    from agent_vision.images import path_to_data_uri

    agent = VisionAgent()
    data_uri = path_to_data_uri(path)
    user_input = f"{prompt}\n{data_uri}" if prompt else data_uri
    analysis = await _collect(agent.run(skill_id, user_input))
    return {"skill": skill_id, "analysis": analysis, "chars": len(analysis)}


@celery_app.task(name="workers.tasks.vision.analyze_image", bind=True)
def analyze_image(  # type: ignore[no-untyped-def]
    self,
    image_path: str,
    prompt: str = "",
    skill_id: str = "analyze-image",
):
    """Analyze a staged image with the multimodal Vision agent, reporting progress."""
    task_id = self.request.id
    publish(task_id, status="started", progress=0, message="Starting image analysis")
    path = Path(image_path)
    try:
        if not path.is_file():
            raise FileNotFoundError(f"staged image not found: {image_path}")
        publish(
            task_id,
            status="progress",
            progress=30,
            message=f"Analyzing {path.name} ({skill_id})",
        )
        result = _run_async(_analyze(path, prompt, skill_id))
        publish(
            task_id,
            status="success",
            progress=100,
            message=f"Analysis complete ({result['chars']} chars)",
            result=result,
        )
        return result
    except Exception as exc:  # noqa: BLE001 — surface to the UI, then re-raise
        publish(task_id, status="failure", message=str(exc), error=str(exc))
        raise
    finally:
        # The staged upload is consumed; remove it whether we succeeded or not.
        with contextlib.suppress(OSError):
            path.unlink()
