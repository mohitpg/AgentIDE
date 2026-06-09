"""Pydantic DTOs for the /sessions REST API and the /chat WebSocket."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    run_id: str | None = None
    created_at: datetime


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionRead):
    messages: list[MessageRead] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """Inbound WebSocket frame: a user message on a given session."""

    session_id: uuid.UUID
    content: str = Field(min_length=1)
