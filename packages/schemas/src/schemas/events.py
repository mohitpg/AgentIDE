"""
Unified WebSocket/SSE streaming event schema.

Every service — agents, orchestrator, workers — emits events in this format.
The Electron UI handles all of them in a single switch on `event.type`.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    TOKEN = "token"  # LLM token chunk
    AGENT_STEP = "agent_step"  # LangGraph node entry/exit
    TOOL_CALL = "tool_call"  # MCP / native tool invoked
    TOOL_RESULT = "tool_result"  # Tool response
    TASK_PROGRESS = "task_progress"  # Celery async task progress
    ERROR = "error"  # Recoverable or fatal error
    DONE = "done"  # Run complete


class Channel(StrEnum):
    """Which stream a token belongs to (carried in ``TokenEvent.data['channel']``).

    The supervisor loop streams intermediate specialist output on ``THINKING`` and
    the composed final answer on ``FINAL``. The UI collapses ``THINKING`` tokens
    into a disclosure and renders ``FINAL`` as the answer body. Absent ⇒ ``FINAL``,
    so this is backward-compatible with emitters that don't set it.
    """

    THINKING = "thinking"
    FINAL = "final"


class StreamEvent(BaseModel):
    """Base event. All fields are always present on the wire."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: EventType
    run_id: str
    data: dict[str, Any] = Field(default_factory=dict)


# ── Typed sub-classes (document the `data` shape) ──────────────────────────


class TokenEvent(StreamEvent):
    """data: {"content": str, "index": int, "channel": "thinking"|"final"}

    ``channel`` defaults to ``"final"`` when absent (see ``Channel``).
    """

    type: EventType = EventType.TOKEN


class AgentStepEvent(StreamEvent):
    """data: {"agent": str, "node": str, "phase": "enter"|"exit", "state_keys": list[str]}"""

    type: EventType = EventType.AGENT_STEP


class ToolCallEvent(StreamEvent):
    """data: {"tool": str, "input": dict, "call_id": str, "agent": str}"""

    type: EventType = EventType.TOOL_CALL


class ToolResultEvent(StreamEvent):
    """data: {"call_id": str, "output": Any, "error": str | None}"""

    type: EventType = EventType.TOOL_RESULT


class TaskProgressEvent(StreamEvent):
    """
    Celery async-task lifecycle. One event type carries the whole
    lifecycle; switch on ``data["status"]``.

    data: {
        "task_id": str,
        "status": "pending"|"started"|"progress"|"success"|"failure"|"revoked",
        "progress": int (0-100),
        "message": str,
        "result": Any | None,   # present on success
        "error": str | None,    # present on failure
    }
    """

    type: EventType = EventType.TASK_PROGRESS


class ErrorEvent(StreamEvent):
    """data: {"code": str, "message": str, "recoverable": bool}"""

    type: EventType = EventType.ERROR


class DoneEvent(StreamEvent):
    """data: {"total_tokens": int, "duration_ms": int, "agent": str}"""

    type: EventType = EventType.DONE
