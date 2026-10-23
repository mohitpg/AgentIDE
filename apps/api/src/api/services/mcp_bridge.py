"""
MCP bridge hub: relays agents' tool calls to the user's desktop.

MCP servers run on the user's machine, inside the desktop app. The desktop holds
one WebSocket to the gateway (`/mcp/bridge/ws`), reports the servers and tools it
has (its inventory), and runs the calls relayed to it. Agents never reach the
desktop directly: they call `POST /mcp/relay/call` on any gateway pod, and the
hub routes the call to whichever pod holds that user's socket.

Routing goes through Redis, like `TaskEventHub`:

  mcp:inv:{user}     the inventory plus the owning connection, with a TTL the
                     owning pod refreshes while its socket is open
  mcp:seq:{user}     a counter that orders a user's connections
  mcp:call:{user}    call / cancel / takeover messages for that user's desktop
  mcp:reply:{call}   the desktop's result for one call

Every pod pattern-subscribes to both channel families and acts only on its own
sockets and its own pending calls. One desktop per user is live at a time: the
newest connection takes over, and older ones are closed with 4409, so a call
never runs twice.

Server commands never leave the desktop. The inventory holds only server ids,
exposure rules and tool schemas.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from mcp_manager import ExposedTool, MCPToolInfo, exposed_name
from pydantic import ValidationError
from redis.exceptions import WatchError

from api.schemas.mcp import InventoryServer, RelayCallResult

logger = logging.getLogger(__name__)

INVENTORY_TTL_SECONDS = 90
_REFRESH_SECONDS = 30.0
_CALL_PREFIX = "mcp:call:"
_REPLY_PREFIX = "mcp:reply:"
# A tool's output goes through Redis and into the model's context; cap it.
_MAX_RESULT_CHARS = 200_000

# Close code for a desktop connection superseded by a newer one for the same user.
WS_REPLACED = 4409
_REPLACED_REASON = "replaced by another AgentIDE connection"

_OFFLINE = (
    "The user's AgentIDE desktop app isn't connected, so their local tools "
    "(files, git, …) are unavailable right now."
)


def _inventory_key(user_id: uuid.UUID) -> str:
    return f"mcp:inv:{user_id}"


def _seq_key(user_id: uuid.UUID) -> str:
    return f"mcp:seq:{user_id}"


@dataclass(frozen=True)
class CloseFrame:
    """Queued on a connection's outbox to make the router close the socket."""

    code: int
    reason: str


@dataclass(eq=False)
class BridgeConnection:
    """One desktop socket as the hub sees it. The router sends what `outbox` holds."""

    user_id: uuid.UUID
    seq: int
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    outbox: asyncio.Queue[dict[str, Any] | CloseFrame] = field(default_factory=asyncio.Queue)
    # The last inventory the desktop pushed (None until its first push).
    servers: list[InventoryServer] | None = None
    # Calls sent to the desktop and not answered yet.
    pending: set[str] = field(default_factory=set)
    closed: bool = False

    def send(self, frame: dict[str, Any]) -> None:
        if not self.closed:
            self.outbox.put_nowait(frame)

    def close(self, code: int, reason: str) -> None:
        if not self.closed:
            self.closed = True
            self.outbox.put_nowait(CloseFrame(code, reason))


@dataclass(frozen=True)
class _Inventory:
    conn_id: str
    seq: int
    servers: list[InventoryServer]


def _parse_inventory(raw: str | None) -> _Inventory | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return _Inventory(
            conn_id=str(data["conn_id"]),
            seq=int(data["seq"]),
            servers=[InventoryServer.model_validate(s) for s in data.get("servers") or []],
        )
    except (ValueError, KeyError, TypeError, ValidationError):
        logger.warning("dropping malformed MCP inventory")
        return None


def tools_for_agent(servers: list[InventoryServer], agent_id: str) -> list[ExposedTool]:
    """The inventory's tools that ``agent_id`` may use, under namespaced names."""
    out: list[ExposedTool] = []
    for server in servers:
        rules = server.exposure()
        if not rules.allows_agent(agent_id):
            continue
        for tool in server.tools:
            if rules.allows_tool(tool.name):
                info = MCPToolInfo(tool.name, tool.description, tool.input_schema)
                out.append(ExposedTool(exposed_name(server.id, tool.name), server.id, info))
    return out


def resolve_tool(tools: list[ExposedTool], name: str) -> ExposedTool | None:
    """Match a tool-call name: the namespaced name first, then the bare tool name.

    Some providers strip the `{server}__` prefix (see `MCPManager._resolve`).
    """
    for et in tools:
        if et.exposed_name == name:
            return et
    bare = [et for et in tools if et.tool.name == name]
    if len(bare) > 1:
        logger.warning("tool name '%s' is ambiguous across servers; using '%s'",
                       name, bare[0].exposed_name)
    return bare[0] if bare else None


class MCPBridgeHub:
    def __init__(
        self,
        redis: aioredis.Redis,
        *,
        timeout_seconds: float = 120.0,
        refresh_seconds: float = _REFRESH_SECONDS,
    ) -> None:
        self._redis = redis
        self._timeout = timeout_seconds
        self._refresh_seconds = refresh_seconds
        self._local: dict[str, set[BridgeConnection]] = {}
        self._waiters: dict[str, asyncio.Future[RelayCallResult]] = {}
        self._pubsub: Any = None
        self._tasks: list[asyncio.Task[None]] = []

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self) -> None:
        try:
            self._pubsub = await self._subscribe()
        except Exception as exc:  # Redis down at boot: the listener keeps retrying
            logger.warning("MCP bridge hub can't subscribe yet (%s); retrying", exc)
        self._tasks = [
            asyncio.create_task(self._listen()),
            asyncio.create_task(self._refresh_loop()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        for conns in list(self._local.values()):
            for conn in conns:
                conn.close(1012, "server restarting")
        await self._close_pubsub()

    # ── desktop side (the /mcp/bridge/ws router) ──────────────────────────────
    async def attach(self, user_id: uuid.UUID) -> BridgeConnection:
        """Register a newly authenticated desktop socket; it supersedes older ones."""
        seq = int(await self._redis.incr(_seq_key(user_id)))
        conn = BridgeConnection(user_id=user_id, seq=seq)
        self._local.setdefault(str(user_id), set()).add(conn)
        await self._publish(
            _CALL_PREFIX + str(user_id),
            {"kind": "takeover", "conn_id": conn.conn_id, "seq": seq},
        )
        return conn

    async def detach(self, conn: BridgeConnection) -> None:
        """The socket is gone: fail its unanswered calls and drop its inventory."""
        conn.closed = True
        conns = self._local.get(str(conn.user_id))
        if conns is not None:
            conns.discard(conn)
            if not conns:
                del self._local[str(conn.user_id)]
        for call_id in list(conn.pending):
            with contextlib.suppress(Exception):
                await self._reply(call_id, RelayCallResult(
                    ok=False, error="The desktop disconnected before the tool finished."))
        conn.pending.clear()
        try:
            await self._delete_inventory(conn)
        except Exception as exc:  # the TTL removes it anyway
            logger.warning("could not drop MCP inventory for %s: %s", conn.user_id, exc)

    async def set_inventory(self, conn: BridgeConnection, servers: list[InventoryServer]) -> None:
        conn.servers = servers
        if not await self._write_inventory(conn):
            conn.close(WS_REPLACED, _REPLACED_REASON)

    async def complete(self, conn: BridgeConnection, call_id: str, result: RelayCallResult) -> bool:
        """Forward the desktop's result for one of its calls; False if not its call."""
        if call_id not in conn.pending:
            return False  # unknown, cancelled, or never sent to this desktop
        conn.pending.discard(call_id)
        if len(result.content) > _MAX_RESULT_CHARS:
            result = result.model_copy(update={
                "content": result.content[:_MAX_RESULT_CHARS] + "\n[output truncated]"})
        await self._reply(call_id, result)
        return True

    # ── agent side (the /mcp/relay routes) ────────────────────────────────────
    async def tools(self, user_id: uuid.UUID, agent_id: str) -> list[ExposedTool]:
        """The user's desktop tools ``agent_id`` may use; [] if the desktop is offline."""
        inventory = await self._read_inventory(user_id)
        return tools_for_agent(inventory.servers, agent_id) if inventory else []

    async def call(
        self, user_id: uuid.UUID, agent_id: str, name: str, arguments: dict[str, Any]
    ) -> RelayCallResult:
        """Run a tool on the user's desktop and wait for its result."""
        inventory = await self._read_inventory(user_id)
        if inventory is None:
            return RelayCallResult(ok=False, error=_OFFLINE)
        tool = resolve_tool(tools_for_agent(inventory.servers, agent_id), name)
        if tool is None:
            return RelayCallResult(ok=False, error=(
                f"No tool named '{name}' is available to the {agent_id} agent "
                "on the user's desktop."))

        call_id = uuid.uuid4().hex
        future: asyncio.Future[RelayCallResult] = asyncio.get_running_loop().create_future()
        self._waiters[call_id] = future
        channel = _CALL_PREFIX + str(user_id)
        try:
            await self._publish(channel, {
                "kind": "call",
                "conn_id": inventory.conn_id,
                "call_id": call_id,
                "agent_id": agent_id,
                "server_id": tool.server_id,
                "tool": tool.tool.name,
                "arguments": arguments,
            })
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError:
            # Tell the desktop to drop it (e.g. close a confirm dialog nobody answered).
            with contextlib.suppress(Exception):
                await self._publish(channel, {
                    "kind": "cancel", "conn_id": inventory.conn_id, "call_id": call_id})
            return RelayCallResult(ok=False, error=(
                f"The desktop didn't answer within {self._timeout:g}s "
                "(the user may not have approved the call)."))
        finally:
            self._waiters.pop(call_id, None)

    # ── pub/sub ───────────────────────────────────────────────────────────────
    async def _subscribe(self) -> Any:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(f"{_CALL_PREFIX}*", f"{_REPLY_PREFIX}*")
        return pubsub

    async def _close_pubsub(self) -> None:
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None

    async def _listen(self) -> None:
        while True:
            try:
                if self._pubsub is None:
                    self._pubsub = await self._subscribe()
                    logger.info("MCP bridge hub subscribed")
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0)
                if message is not None and message.get("type") == "pmessage":
                    self._dispatch(message["channel"], message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Redis blip: back off and resubscribe
                logger.warning("MCP bridge hub pub/sub error (%s); retrying in 2s", exc)
                await self._close_pubsub()
                await asyncio.sleep(2)

    async def _publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message))

    async def _reply(self, call_id: str, result: RelayCallResult) -> None:
        await self._publish(_REPLY_PREFIX + call_id, result.model_dump(mode="json"))

    def _dispatch(self, channel: str, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(message, dict):
            return
        if channel.startswith(_REPLY_PREFIX):
            future = self._waiters.get(channel[len(_REPLY_PREFIX):])
            if future is not None and not future.done():
                try:
                    future.set_result(RelayCallResult.model_validate(message))
                except ValidationError:
                    future.set_result(RelayCallResult(
                        ok=False, error="malformed reply from the desktop"))
            return
        for conn in list(self._local.get(channel[len(_CALL_PREFIX):], ())):
            _deliver(conn, message)

    # ── inventory in Redis ────────────────────────────────────────────────────
    async def _read_inventory(self, user_id: uuid.UUID) -> _Inventory | None:
        return _parse_inventory(await self._redis.get(_inventory_key(user_id)))

    async def _write_inventory(self, conn: BridgeConnection) -> bool:
        """Store the connection's inventory unless a newer connection owns the key.

        Returns False when the connection has been superseded.
        """
        key = _inventory_key(conn.user_id)
        value = json.dumps({
            "conn_id": conn.conn_id,
            "seq": conn.seq,
            "servers": [s.model_dump(mode="json") for s in conn.servers or []],
        })
        for _ in range(3):
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    current = _parse_inventory(await pipe.get(key))
                    if current is not None and current.seq > conn.seq:
                        return False
                    pipe.multi()
                    pipe.set(key, value, ex=INVENTORY_TTL_SECONDS)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue  # another connection wrote meanwhile; re-check
        return True  # still contended; the next refresh settles it

    async def _delete_inventory(self, conn: BridgeConnection) -> None:
        key = _inventory_key(conn.user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            # WatchError: it changed meanwhile, so it isn't ours any more.
            with contextlib.suppress(WatchError):
                await pipe.watch(key)
                current = _parse_inventory(await pipe.get(key))
                if current is None or current.conn_id != conn.conn_id:
                    return  # a newer connection owns it now
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()

    async def _refresh_loop(self) -> None:
        """Keep each live connection's inventory from expiring."""
        while True:
            await asyncio.sleep(self._refresh_seconds)
            for conns in list(self._local.values()):
                for conn in list(conns):
                    if conn.closed or conn.servers is None:
                        continue
                    try:
                        if not await self._write_inventory(conn):
                            conn.close(WS_REPLACED, _REPLACED_REASON)
                    except Exception as exc:
                        logger.warning("MCP inventory refresh for %s failed: %s",
                                       conn.user_id, exc)


def _deliver(conn: BridgeConnection, message: dict[str, Any]) -> None:
    """Apply one `mcp:call:{user}` message to a local connection of that user."""
    kind = message.get("kind")
    if kind == "takeover":
        seq = message.get("seq")
        if isinstance(seq, int) and seq > conn.seq:
            conn.close(WS_REPLACED, _REPLACED_REASON)
        return
    if conn.closed or message.get("conn_id") != conn.conn_id:
        return
    call_id = str(message.get("call_id") or "")
    if kind == "call" and call_id:
        conn.pending.add(call_id)
        conn.send({
            "type": "call",
            "id": call_id,
            "agent_id": message.get("agent_id"),
            "server_id": message.get("server_id"),
            "tool": message.get("tool"),
            "arguments": message.get("arguments") or {},
        })
    elif kind == "cancel" and call_id in conn.pending:
        conn.pending.discard(call_id)
        conn.send({"type": "cancel", "id": call_id})
