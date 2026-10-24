"""
/mcp — the bridge between cloud agents and the MCP servers on users' desktops.

  WS   /mcp/bridge/ws     the desktop app's connection (one per signed-in user)
  GET  /mcp/relay/tools   tools the user's desktop exposes to an agent   (agents only)
  POST /mcp/relay/call    run one of those tools on the desktop          (agents only)

Socket protocol, after the first-frame auth (`{"type": "auth", "token"}`):

  desktop → gateway
    {"type": "inventory", "servers": [{id, name, allowed_agents, tool_allowlist,
                                       tools: [{name, description, input_schema}]}]}
    {"type": "result", "id", "ok", "content" | "error"}
    {"type": "ping"}
  gateway → desktop
    {"type": "ready"}
    {"type": "call", "id", "agent_id", "server_id", "tool", "arguments"}
    {"type": "cancel", "id"}          the agent stopped waiting; drop the call
    {"type": "pong"} | {"type": "error", "message"}

A newer connection for the same user closes this one with 4409. The relay routes
take the service token plus `X-On-Behalf-Of: <user id>`, like the agents' other
calls to the gateway (see `api.deps`). Routing lives in `api.services.mcp_bridge`.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError
from starlette.requests import HTTPConnection

from api.deps import Principal, authenticate_websocket_user, require_service
from api.schemas.mcp import InventoryServer, RelayCallRequest, RelayCallResult, RelayTool
from api.services.mcp_bridge import BridgeConnection, CloseFrame, MCPBridgeHub

router = APIRouter()
_servers = TypeAdapter(list[InventoryServer])


def get_hub(connection: HTTPConnection) -> MCPBridgeHub:
    return connection.app.state.mcp_hub


def _on_behalf_of(principal: Principal) -> uuid.UUID:
    if principal.user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-On-Behalf-Of header required")
    return principal.user.id


@router.get("/relay/tools", response_model=list[RelayTool])
async def relay_tools(
    agent_id: str,
    principal: Principal = Depends(require_service),
    hub: MCPBridgeHub = Depends(get_hub),
) -> list[RelayTool]:
    """The user's desktop tools this agent may use; empty when the desktop is offline."""
    return [
        RelayTool(
            name=et.exposed_name,
            server_id=et.server_id,
            tool=et.tool.name,
            description=et.tool.description,
            input_schema=et.tool.input_schema,
        )
        for et in await hub.tools(_on_behalf_of(principal), agent_id)
    ]


@router.post("/relay/call", response_model=RelayCallResult)
async def relay_call(
    body: RelayCallRequest,
    principal: Principal = Depends(require_service),
    hub: MCPBridgeHub = Depends(get_hub),
) -> RelayCallResult:
    """Run a tool on the user's desktop. Tool failures come back as `ok: false`."""
    return await hub.call(_on_behalf_of(principal), body.agent_id, body.name, body.arguments)


@router.websocket("/bridge/ws")
async def bridge_ws(websocket: WebSocket, hub: MCPBridgeHub = Depends(get_hub)) -> None:
    await websocket.accept()
    user_id = await authenticate_websocket_user(websocket)
    if user_id is None:
        return
    conn = await hub.attach(user_id)
    conn.send({"type": "ready"})

    receiver = asyncio.create_task(_receive(websocket, hub, conn))
    sender = asyncio.create_task(_send(websocket, conn))
    try:
        await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (receiver, sender):
            task.cancel()
        await asyncio.gather(receiver, sender, return_exceptions=True)
        await hub.detach(conn)


async def _send(websocket: WebSocket, conn: BridgeConnection) -> None:
    """Write the hub's frames to the socket; a CloseFrame ends the connection."""
    while True:
        item = await conn.outbox.get()
        if isinstance(item, CloseFrame):
            await websocket.close(code=item.code, reason=item.reason)
            return
        await websocket.send_json(item)


async def _receive(websocket: WebSocket, hub: MCPBridgeHub, conn: BridgeConnection) -> None:
    while True:
        try:
            frame = json.loads(await websocket.receive_text())
        except WebSocketDisconnect:
            return
        except (ValueError, KeyError):  # not JSON, or a binary frame
            conn.send({"type": "error", "message": "frames must be JSON text"})
            continue
        if not isinstance(frame, dict):
            continue

        kind = frame.get("type")
        if kind == "inventory":
            try:
                servers = _servers.validate_python(frame.get("servers") or [])
            except ValidationError as exc:
                conn.send({"type": "error", "message": f"invalid inventory: {exc}"})
                continue
            await hub.set_inventory(conn, servers)
        elif kind == "result":
            error = frame.get("error")
            result = RelayCallResult(
                ok=bool(frame.get("ok")),
                content=str(frame.get("content") or ""),
                error=str(error) if error else None,
            )
            await hub.complete(conn, str(frame.get("id") or ""), result)
        elif kind == "ping":
            conn.send({"type": "pong"})
