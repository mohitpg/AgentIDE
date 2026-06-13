"""
RegistrationManager: register payload, heartbeat, 404 re-register, deregister.

Uses httpx.MockTransport so no gateway is needed.
"""

from __future__ import annotations

import json

import httpx
from agent_common.registration import RegistrationManager

CARD = {
    "name": "Code",
    "url": "http://localhost:8102",
    "version": "0.1.0",
    "skills": [{"id": "generate", "name": "Generate"}],
}


def _manager(handler, **kwargs) -> RegistrationManager:
    return RegistrationManager(
        agent_id="code",
        card=CARD,
        gateway_url="http://gateway:8000",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_register_posts_card_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"agent_id": "code"})

    await _manager(handler).register()

    assert seen["url"] == "http://gateway:8000/agents/register"
    assert seen["json"]["agent_id"] == "code"
    assert seen["json"]["card"]["name"] == "Code"
    assert seen["json"]["url"] == "http://localhost:8102"


async def test_heartbeat_404_triggers_reregister():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(404)
        return httpx.Response(200, json={"agent_id": "code"})

    await _manager(handler)._send_heartbeat()

    assert calls == ["POST /agents/code/heartbeat", "POST /agents/register"]


async def test_deregister_calls_delete():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(204)

    await _manager(handler).deregister()
    assert calls == ["DELETE /agents/code"]


async def test_start_swallows_gateway_errors_and_runs_heartbeat_loop():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gateway down")

    manager = _manager(handler, heartbeat_seconds=1)
    # Should not raise even though registration fails.
    await manager.start()
    assert manager._task is not None
    await manager.stop()
