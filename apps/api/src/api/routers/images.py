"""
/images — chat image attachments (disk ↔ DB mapping).

  POST /images        stage an uploaded image on disk + insert a row → return the
                      `<Image at db.images.{id}>` reference the composer embeds.
  GET  /images/{id}    stream the staged bytes back (Vision agent + UI thumbnail).

This replaces shipping base64 data URIs through the chat socket: the multi-KB blob
is stored once on disk and referenced by a compact token, so neither the WebSocket
frame nor the persisted `messages` row carries the image. Rows are scoped to the
chat session and cascade-deleted with the conversation (the staged files are swept
in `routers/sessions.delete_session`).
"""

from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.db.session import get_db
from api.models.conversation import Conversation
from api.models.image import Image
from api.schemas.images import ImageUploadResponse

router = APIRouter()


def image_ref(image_id: uuid.UUID | str) -> str:
    """The reference token embedded in chat content for a stored image."""
    return f"<Image at db.images.{image_id}>"


def _upload_dir(settings: Settings) -> Path:
    """Where uploaded image bytes are staged (shared host with the agents)."""
    base = (
        Path(settings.image_upload_dir)
        if settings.image_upload_dir
        else Path(tempfile.gettempdir()) / "agentide_images"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "image"


def _validate_upload(data: bytes, settings: Settings) -> None:
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.image_max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Image exceeds {settings.image_max_upload_bytes} byte limit",
        )


async def _resolve_session(
    db: AsyncSession, session_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Keep the session link only if that conversation exists"""
    if session_id is None:
        return None
    exists = await db.get(Conversation, session_id)
    return session_id if exists is not None else None


@router.post("", response_model=ImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse:
    """Stage an attached image and return its `<Image at db.images.{id}>` reference."""
    settings = get_settings()
    data = await file.read()
    _validate_upload(data, settings)
    session_id = await _resolve_session(db, session_id)

    image_id = uuid.uuid4()
    filename = file.filename or "image"
    content_type = file.content_type or "image/png"
    path = _upload_dir(settings) / f"{image_id}__{_safe_name(filename)}"
    path.write_bytes(data)

    db.add(
        Image(
            id=image_id,
            filename=filename,
            content_type=content_type,
            path=str(path),
            byte_size=len(data),
            session_id=session_id,
        )
    )
    await db.commit()

    return ImageUploadResponse(
        id=image_id,
        ref=image_ref(image_id),
        filename=filename,
        content_type=content_type,
    )


@router.get("/{image_id}")
async def get_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Serve a staged image — used by the Vision agent and the UI thumbnail."""
    image = await db.get(Image, image_id)
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    path = Path(image.path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image file missing")
    return FileResponse(path, media_type=image.content_type, filename=image.filename)
