"""
/chat/ws – WebSocket streaming chat.

Per inbound frame ({"session_id", "content"}):
  1. persist the user message,
  2. run the orchestrator graph (thread = session_id, so history is restored),
  3. relay every StreamEvent to the client as JSON,
  4. persist the assembled assistant message.

All output uses the shared StreamEvent schema (token / agent_step / done / error)
so the UI switches on `event.type` uniformly.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from schemas.events import DoneEvent, ErrorEvent, TokenEvent

from api.db.session import AsyncSessionLocal
from api.models.conversation import Conversation
from api.models.message import Message
from api.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()


async def _send_error(ws: WebSocket, run_id: str, code: str, message: str) -> None:
    event = ErrorEvent(run_id=run_id, data={"code": code, "message": message, "recoverable": True})
    await ws.send_json(event.model_dump(mode="json"))


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    await websocket.accept()
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
                    await _send_error(websocket, run_id, "not_found", "Session not found")
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
                    conversation.title = req.content[:60]
                user_id = str(conversation.user_id) if conversation.user_id else ""
                await db.commit()

            # ── Stream the assistant turn ──────────────────────────────────────
            assistant_text = ""
            total_tokens = 0
            try:
                async for event in orchestrator.stream_chat(
                    content=req.content,
                    run_id=run_id,
                    session_id=str(req.session_id),
                    user_id=user_id,
                ):
                    await websocket.send_json(event.model_dump(mode="json"))
                    if isinstance(event, TokenEvent):
                        assistant_text += event.data["content"]
                    elif isinstance(event, DoneEvent):
                        total_tokens = event.data.get("total_tokens", 0)
            except Exception as exc:
                logger.exception("chat run %s failed", run_id)
                await _send_error(websocket, run_id, "run_failed", str(exc))
                continue

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
