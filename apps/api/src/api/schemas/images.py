"""Pydantic DTOs for the /images API."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ImageUploadResponse(BaseModel):
    """Returned by POST /images after the bytes are staged and the row is written.

    `ref` is the exact token the composer embeds in the chat message; the Vision
    agent extracts it and resolves the bytes back via GET /images/{id}.
    """

    id: uuid.UUID
    ref: str
    filename: str
    content_type: str
