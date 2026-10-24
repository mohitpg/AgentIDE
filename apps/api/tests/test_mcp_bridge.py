"""
MCP bridge: desktop inventory, per-agent exposure, call routing across gateway
pods, takeover by a newer desktop, timeouts, and the HTTP / WebSocket surfaces.

Redis is fakeredis, with one FakeServer shared by every hub (each hub plays a
gateway pod). The desktop is played through the hub-side connection's outbox,
or through a fake WebSocket for the router tests.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import fakeredis
import pytest
from api.main import app
from api.routers import mcp_bridge as bridge_router
from api.routers.mcp_bridge import get_hub
from api.schemas.mcp import InventoryServer, InventoryTool, RelayCallResult
from api.services.mcp_bridge import WS_REPLACED, BridgeConnection, CloseFrame, MCPBridgeHub
from fastapi import WebSocketDisconnect
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def make_hub():
    """Build hubs that share one Redis, like gateway pods."""
    server = fakeredis.FakeServer()
    made: list[tuple[MCPBridgeHub, fakeredis.FakeAsyncRedis]] = []

    async def factory(*, timeout: float = 2.0) -> MCPBridgeHub:
        redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        hub = MCPBridgeHub(redis, timeout_seconds=timeout)
        await hub.start()
        made.append((hub, redis))
        return hub

    yield factory
    for hub, redis in made:
        await hub.stop()
        await redis.aclose()


def _inventory(**exposure) -> list[InventoryServer]:
    return [
        InventoryServer(
            id="filesystem",
            name="Filesystem",
            tools=[
                InventoryTool(name="read_text_file", description="read"),
                InventoryTool(name="write_file", description="write"),
            ],
            **exposure,
        )
    ]


async def _frame(conn: BridgeConnection):
    return await asyncio.wait_for(conn.outbox.get(), 2.0)


async def _desktop(
    hub: MCPBridgeHub, user_id: uuid.UUID, servers: list[InventoryServer] | None = None
) -> BridgeConnection:
    conn = await hub.attach(user_id)
    await hub.set_inventory(conn, _inventory() if servers is None else servers)
    return conn


async def _answer_next(hub: MCPBridgeHub, conn: BridgeConnection, **result) -> dict:
    """Play the desktop: take the next call off the socket and answer it."""
    frame = await _frame(conn)
    assert frame["type"] == "call"
    assert await hub.complete(conn, frame["id"], RelayCallResult(**result))
    return frame


# ── the hub ───────────────────────────────────────────────────────────────────
async def test_tools_follow_the_exposure_rules(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    servers = _inventory(allowed_agents=["code"], tool_allowlist=["read_text_file"])
    await _desktop(hub, user, servers)

    assert [t.exposed_name for t in await hub.tools(user, "code")] == [
        "filesystem__read_text_file"
    ]
    assert await hub.tools(user, "web") == []


async def test_offline_desktop_has_no_tools(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()

    assert await hub.tools(user, "code") == []
    result = await hub.call(user, "code", "filesystem__read_text_file", {})
    assert not result.ok
    assert "isn't connected" in (result.error or "")


async def test_call_is_routed_to_the_pod_holding_the_desktop(make_hub):
    holder, relay = await make_hub(), await make_hub()
    user = uuid.uuid4()
    conn = await _desktop(holder, user)

    call = asyncio.create_task(
        relay.call(user, "code", "filesystem__read_text_file", {"path": "a.py"})
    )
    frame = await _answer_next(holder, conn, ok=True, content="hello")

    assert frame == {
        "type": "call",
        "id": frame["id"],
        "agent_id": "code",
        "server_id": "filesystem",
        "tool": "read_text_file",
        "arguments": {"path": "a.py"},
    }
    assert await call == RelayCallResult(ok=True, content="hello")


async def test_bare_tool_name_falls_back(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    conn = await _desktop(hub, user)

    call = asyncio.create_task(hub.call(user, "code", "read_text_file", {}))
    frame = await _answer_next(hub, conn, ok=True, content="x")

    assert frame["tool"] == "read_text_file"
    assert (await call).ok


async def test_hidden_tool_is_refused_without_asking_the_desktop(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    conn = await _desktop(hub, user, _inventory(tool_allowlist=["read_text_file"]))

    result = await hub.call(user, "code", "filesystem__write_file", {"path": "a"})

    assert not result.ok
    assert "No tool named" in (result.error or "")
    assert conn.outbox.empty()


async def test_timeout_cancels_the_call_on_the_desktop(make_hub):
    hub = await make_hub(timeout=0.2)
    user = uuid.uuid4()
    conn = await _desktop(hub, user)

    result = await hub.call(user, "code", "filesystem__write_file", {"path": "a"})

    assert not result.ok
    assert "didn't answer" in (result.error or "")
    call, cancel = await _frame(conn), await _frame(conn)
    assert call["type"] == "call"
    assert cancel == {"type": "cancel", "id": call["id"]}
    # A late answer goes nowhere.
    assert not await hub.complete(conn, call["id"], RelayCallResult(ok=True))


async def test_newer_desktop_takes_over(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    old = await _desktop(hub, user)
    new = await _desktop(hub, user, _inventory(tool_allowlist=["read_text_file"]))

    closing = await _frame(old)
    assert isinstance(closing, CloseFrame)
    assert closing.code == WS_REPLACED

    call = asyncio.create_task(hub.call(user, "code", "filesystem__read_text_file", {}))
    await _answer_next(hub, new, ok=True, content="from the new desktop")
    assert (await call).content == "from the new desktop"
    assert old.outbox.empty()


async def test_superseded_desktop_cannot_reclaim_the_inventory(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    old = await hub.attach(user)
    await _desktop(hub, user, _inventory(tool_allowlist=["read_text_file"]))

    await hub.set_inventory(old, _inventory())  # pushed after the newer one connected

    assert old.closed
    assert [t.tool.name for t in await hub.tools(user, "code")] == ["read_text_file"]


async def test_disconnect_fails_pending_calls_and_drops_the_inventory(make_hub):
    hub = await make_hub()
    user = uuid.uuid4()
    conn = await _desktop(hub, user)

    call = asyncio.create_task(hub.call(user, "code", "filesystem__write_file", {}))
    await _frame(conn)  # the call reached the desktop
    await hub.detach(conn)

    result = await call
    assert not result.ok
    assert "disconnected" in (result.error or "")
    assert await hub.tools(user, "code") == []


# ── relay routes (agents → gateway) ───────────────────────────────────────────
@pytest.fixture
async def relay(make_hub):
    hub = await make_hub()
    app.dependency_overrides[get_hub] = lambda: hub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c, hub
    app.dependency_overrides.pop(get_hub, None)


async def test_relay_lists_the_users_tools(relay, auth):
    client, hub = relay
    user = auth.make_user()
    await _desktop(hub, user.id)
    auth.as_service(on_behalf_of=user)

    resp = await client.get("/mcp/relay/tools", params={"agent_id": "code"})

    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()] == [
        "filesystem__read_text_file",
        "filesystem__write_file",
    ]
    assert resp.json()[0]["server_id"] == "filesystem"


async def test_relay_call_round_trip(relay, auth):
    client, hub = relay
    user = auth.make_user()
    conn = await _desktop(hub, user.id)
    auth.as_service(on_behalf_of=user)

    post = asyncio.create_task(client.post("/mcp/relay/call", json={
        "agent_id": "code", "name": "filesystem__read_text_file", "arguments": {"path": "a"},
    }))
    await _answer_next(hub, conn, ok=True, content="data")
    resp = await post

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "content": "data", "error": None}


async def test_relay_needs_the_user(relay, auth):
    client, _ = relay
    auth.as_service()
    resp = await client.get("/mcp/relay/tools", params={"agent_id": "code"})
    assert resp.status_code == 400


async def test_relay_is_service_only(relay, auth):
    client, _ = relay
    auth.as_user(auth.make_user())
    resp = await client.post("/mcp/relay/call", json={"agent_id": "code", "name": "x"})
    assert resp.status_code == 403


# ── the desktop socket ────────────────────────────────────────────────────────
class FakeSocket:
    """Just enough of a Starlette WebSocket for the bridge router."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: asyncio.Queue[dict] = asyncio.Queue()
        self.closed_with: int | None = None

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        text = await self.inbox.get()
        if text is None:
            raise WebSocketDisconnect(1000)
        return text

    async def send_json(self, data: dict) -> None:
        await self.sent.put(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_with = code

    def push(self, frame: dict) -> None:
        self.inbox.put_nowait(json.dumps(frame))

    def disconnect(self) -> None:
        self.inbox.put_nowait(None)

    async def next_sent(self) -> dict:
        return await asyncio.wait_for(self.sent.get(), 2.0)


@pytest.fixture
def signed_in(monkeypatch):
    """Skip the first-frame auth: every socket belongs to one user."""
    user = uuid.uuid4()

    async def _authenticate(websocket):
        return user

    monkeypatch.setattr(bridge_router, "authenticate_websocket_user", _authenticate)
    return user


async def test_bridge_socket_end_to_end(make_hub, signed_in):
    hub = await make_hub()
    ws = FakeSocket()
    session = asyncio.create_task(bridge_router.bridge_ws(ws, hub=hub))
    assert await ws.next_sent() == {"type": "ready"}

    ws.inbox.put_nowait("not json")
    assert (await ws.next_sent())["type"] == "error"
    ws.push({"type": "inventory", "servers": [
        {"id": "filesystem", "tools": [{"name": "read_text_file"}]},
    ]})
    ws.push({"type": "ping"})
    assert await ws.next_sent() == {"type": "pong"}
    assert [t.exposed_name for t in await hub.tools(signed_in, "code")] == [
        "filesystem__read_text_file"
    ]

    call = asyncio.create_task(hub.call(signed_in, "code", "read_text_file", {"path": "a"}))
    frame = await ws.next_sent()
    ws.push({"type": "result", "id": frame["id"], "ok": True, "content": "hi"})
    assert (await call).content == "hi"

    ws.disconnect()
    await asyncio.wait_for(session, 2.0)
    assert await hub.tools(signed_in, "code") == []


async def test_bridge_rejects_a_bad_inventory(make_hub, signed_in):
    hub = await make_hub()
    ws = FakeSocket()
    session = asyncio.create_task(bridge_router.bridge_ws(ws, hub=hub))
    await ws.next_sent()  # ready

    ws.push({"type": "inventory", "servers": [{"id": "bad__id", "tools": []}]})
    assert (await ws.next_sent())["type"] == "error"
    assert await hub.tools(signed_in, "code") == []

    ws.disconnect()
    await asyncio.wait_for(session, 2.0)


async def test_second_socket_replaces_the_first(make_hub, signed_in):
    hub = await make_hub()
    first, second = FakeSocket(), FakeSocket()
    first_session = asyncio.create_task(bridge_router.bridge_ws(first, hub=hub))
    await first.next_sent()
    second_session = asyncio.create_task(bridge_router.bridge_ws(second, hub=hub))
    await second.next_sent()

    await asyncio.wait_for(first_session, 2.0)
    assert first.closed_with == WS_REPLACED
    assert second.closed_with is None

    second.disconnect()
    await asyncio.wait_for(second_session, 2.0)
