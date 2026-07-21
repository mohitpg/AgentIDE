"""Pydantic DTOs for the /tasks REST API"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    """Request to enqueue an async task."""

    # Logical task type; must be a key in the router's TASK_CATALOG.
    type: str = Field(default="mock", min_length=1, max_length=64)
    label: str | None = None
    # Task-specific keyword args passed to the Celery task (merged over defaults).
    params: dict[str, Any] = Field(default_factory=dict)
    # Optional chat session this task belongs to.
    session_id: uuid.UUID | None = None


class TaskRead(BaseModel):
    """A task record as returned by the REST API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    label: str | None
    queue: str
    status: str
    progress: int
    message: str | None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    session_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
