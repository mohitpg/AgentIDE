"""
/chat/ws – WebSocket streaming chat.

The first frame authenticates the socket (`{"type": "auth", "token",
"project_id"}`, see `api.deps.authenticate_websocket`); every turn after that
runs in that user + project scope. Per inbound frame ({"session_id", "content"}):
  1. check the session belongs to the scope (or create it there), and that any
     attached-image references are the caller's own,
  2. persist the user message,
  3. run the orchestrator graph (thread = session_id, so history is restored),
  4. relay every StreamEvent to the client as JSON,
  5. persist the assembled assistant message.

All output uses the shared StreamEvent schema (token / agent_step / done / error)
so the UI switches on `event.type` uniformly.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from opentelemetry import metrics, trace
from opentelemetry.trace import StatusCode
from pydantic import ValidationError
from schemas.events import DoneEvent, ErrorEvent, TokenEvent
from sqlalchemy import func, select

from api.db.session import AsyncSessionLocal
from api.deps import Scope, authenticate_websocket
from api.models.conversation import Conversation
from api.models.image import Image
from api.models.message import Message
from api.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()
_tracer = trace.get_tracer(__name__)
_turn_duration = metrics.get_meter(__name__).create_histogram(
    "agentide.chat.turn.duration", unit="s", description="End-to-end chat turn latency"
)

# Stored-image reference token
_IMAGE_REF_RE = re.compile(r"<Image at db\.images\.([0-9a-fA-F-]{36})>")


async def _foreign_image_refs(db, content: str, scope: Scope) -> bool:  # type: ignore[no-untyped-def]
    """True if ``content`` references an image outside the caller's scope.

    The Vision agent resolves references on the user's behalf, so a pasted id of
    someone else's image must be stopped here, before it reaches the agent.
    """
    try:
        ids = {uuid.UUID(m) for m in _IMAGE_REF_RE.findall(content)}
    except ValueError:
        return True
    if not ids:
        return False
    owned = await db.scalar(
        select(func.count())
        .select_from(Image)
        .where(
            Image.id.in_(ids),
            Image.user_id == scope.user_id,
            Image.project_id == scope.project_id,
        )
    )
    return owned != len(ids)


async def _send_error(
    ws: WebSocket, run_id: str, code: str, message: str, *, recoverable: bool = True
) -> None:
    event = ErrorEvent(
        run_id=run_id, data={"code": code, "message": message, "recoverable": recoverable}
    )
    await ws.send_json(event.model_dump(mode="json"))


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    scope = await authenticate_websocket(websocket)
    if scope is None:
        return
    orchestrator = websocket.app.state.orchestrator

    try:
        while True:
            raw = await websocket.receive_json()

            try:
                req = ChatRequest.model_validate(raw)
            except ValidationError as exc:
                await _send_error(websocket, "", "invalid_request", str(exc))
                continue

            run_id = str(uuid.uuid4())

            # ── Persist the user turn ──────────────────────────────────────────
            async with AsyncSessionLocal() as db:
                conversation = await db.get(Conversation, req.session_id)
                if conversation is None:
                    conversation = Conversation(
                        id=req.session_id,
                        user_id=scope.user_id,
                        project_id=scope.project_id,
                    )
                    db.add(conversation)
                    await db.flush()
                elif (conversation.user_id, conversation.project_id) != (
                    scope.user_id,
                    scope.project_id,
                ):
                    # Someone else's (or another project's) session id: refuse
                    # rather than append to it and restore its checkpointed history.
                    await _send_error(
                        websocket, run_id, "session_not_found", "Session not found",
                        recoverable=False,
                    )
                    continue
                if await _foreign_image_refs(db, req.content, scope):
                    await _send_error(
                        websocket, run_id, "image_not_found", "Image not found",
                        recoverable=False,
                    )
                    continue

                db.add(
                    Message(
                        conversation_id=req.session_id,
                        role="user",
                        content=req.content,
                    )
                )
                # Derive a title from the first message
                if not conversation.title:
                    title = _IMAGE_REF_RE.sub("", req.content).strip()
                    conversation.title = title[:60] or "Image"
                await db.commit()

            # ── Stream the assistant turn ──────────────────────────────────────
            assistant_text = ""
            total_tokens = 0
            started = time.perf_counter()
            with _tracer.start_as_current_span("chat.turn", attributes={
                "agentide.run_id": run_id,
                "session.id": str(req.session_id),
                "user.id": str(scope.user_id),
                "agentide.project_id": str(scope.project_id),
                "agentide.chat.mode": req.mode,
            }) as span:
                try:
                    async for event in orchestrator.stream_chat(
                        content=req.content,
                        run_id=run_id,
                        session_id=str(req.session_id),
                        user_id=str(scope.user_id),
                        project_id=str(scope.project_id),
                        mode=req.mode,
                    ):
                        await websocket.send_json(event.model_dump(mode="json"))
                        if isinstance(event, TokenEvent):
                            # Persist only the final answer; not intermediate "thinking"
                            if event.data.get("channel", "final") != "thinking":
                                assistant_text += event.data["content"]
                        elif isinstance(event, DoneEvent):
                            total_tokens = event.data.get("total_tokens", 0)
                except Exception as exc:
                    logger.exception("chat run %s failed", run_id)
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    _turn_duration.record(time.perf_counter() - started,
                                          {"agentide.chat.mode": req.mode, "outcome": "error"})
                    await _send_error(websocket, run_id, "run_failed", str(exc))
                    continue
            _turn_duration.record(time.perf_counter() - started,
                                  {"agentide.chat.mode": req.mode, "outcome": "ok"})

            # ── Persist the assistant turn ─────────────────────────────────────
            if assistant_text:
                async with AsyncSessionLocal() as db:
                    db.add(
                        Message(
                            conversation_id=req.session_id,
                            role="assistant",
                            content=assistant_text,
                            run_id=run_id,
                            token_count=total_tokens or None,
                        )
                    )
                    await db.commit()

    except WebSocketDisconnect:
        return
