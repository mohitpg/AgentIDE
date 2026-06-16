"""
Scheduled maintenance tasks (run by Celery Beat).

Phase 3 ships one placeholder: a periodic **registry health check** that asks the
gateway how many specialist agents are currently live vs. registered and logs the
result. It proves the Beat scheduler works and gives an obvious hook for later
maintenance (reaping stale agents, cache warmup, re-index, …).

It deliberately uses only the standard library (`urllib`) so the worker keeps its
slim dependency set and never needs to import the API package.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from workers.app import celery_app

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")


def _get_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — internal URL
        return json.loads(resp.read().decode("utf-8"))


@celery_app.task(name="workers.tasks.maintenance.registry_health_check", bind=True)
def registry_health_check(self) -> dict:  # type: ignore[no-untyped-def]
    """Compare live vs. registered agents and log a one-line health summary."""
    try:
        live = _get_json(f"{GATEWAY_URL}/agents")
        registered = _get_json(f"{GATEWAY_URL}/agents?include_stale=true")
    except Exception as exc:  # gateway down / unreachable — log and move on
        logger.warning("registry health check failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    summary = {
        "ok": True,
        "live": len(live),
        "registered": len(registered),
        "stale": max(0, len(registered) - len(live)),
    }
    logger.info("registry health: %s", summary)
    return summary
