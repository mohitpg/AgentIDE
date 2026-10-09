"""
A2A client wrapper used by the orchestrator's supervisor.

Wraps the a2a-sdk `A2AClient` and normalizes the protocol's streaming events
into the simple `{"type": "token"|"done", "data": {...}}` shape the supervisor
already consumes — so swapping the transport never touches the graph.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from a2a.client import A2AClient
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    SendStreamingMessageRequest,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)


def _message_text(message: Message | None) -> str:
    if message is None:
        return ""
    out = ""
    for part in message.parts:
        root = getattr(part, "root", part)
        if getattr(root, "kind", None) == "text":
            out += root.text
    return out


def _artifact_text(event: TaskArtifactUpdateEvent) -> str:
    out = ""
    for part in event.artifact.parts:
        root = getattr(part, "root", part)
        if getattr(root, "kind", None) == "text":
            out += root.text
    return out


class A2AAgentClient:
    """Talks to an A2A agent over JSON-RPC streaming."""

    def __init__(self, *, auth_token: str = "", timeout: float = 60.0) -> None:
        self._auth_token = auth_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else {}

    def _build_message(
        self, text: str, skill_id: str, scope: dict[str, str] | None = None
    ) -> Message:
        return Message(
            role=Role.user,
            parts=[Part(root=TextPart(text=text))],
            message_id=str(uuid.uuid4()),
            # `user_id` / `project_id` become the agent's row scope
            # (`agent_common.scope`); the bearer token is what makes them trusted.
            metadata={"skill_id": skill_id, **(scope or {})},
        )

    async def stream(
        self,
        base_url: str,
        skill_id: str,
        input_data: dict[str, Any],
        run_id: str | None = None,
        *,
        scope: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # The supervisor packs the turn text under a single key; send it as-is.
        text = input_data.get("text") or next(iter(input_data.values()), "")
        message = self._build_message(text, skill_id, scope)
        request = SendStreamingMessageRequest(
            id=run_id or str(uuid.uuid4()),
            params=MessageSendParams(message=message),
        )

        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as http:
            client = A2AClient(httpx_client=http, url=base_url.rstrip("/"))
            final_text = ""
            async for response in client.send_message_streaming(request):
                result = getattr(response.root, "result", None)
                if isinstance(result, TaskStatusUpdateEvent):
                    chunk = _message_text(result.status.message)
                    if result.status.state == TaskState.working and chunk:
                        yield {"type": "token", "data": {"content": chunk}}
                    elif result.status.state == TaskState.failed:
                        yield {"type": "error", "data": {
                            "message": _message_text(result.status.message)}}
                elif isinstance(result, TaskArtifactUpdateEvent):
                    final_text = _artifact_text(result) or final_text

            yield {"type": "done", "data": {"output": final_text}}
