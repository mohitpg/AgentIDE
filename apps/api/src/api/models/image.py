"""
Uploaded image record — the disk ↔ DB mapping behind the chat image flow.

When a user attaches an image and hits Send, the gateway (`POST /images`) stages
the bytes on disk and inserts one row here, then hands the row id back as a compact
reference ``<Image at db.images.{id}>`` embedded in the chat message. The Vision
agent resolves that reference by fetching the bytes back over ``GET /images/{id}``;
the UI uses the same endpoint to render the thumbnail. So the multi-KB blob never
travels through chat or gets persisted in `messages` — only the reference does.

Rows are scoped to the chat session and cascade-deleted with the conversation; the
staged files are swept on session delete (`routers/sessions.delete_session`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin


class Image(Base, TimestampMixin):
    __tablename__ = "images"

    # Row id == the {id} in the `<Image at db.images.{id}>` reference.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Absolute path of the staged file on disk (read by GET /images/{id}).
    path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The chat session the image was uploaded for. Cascade so deleting a
    # conversation removes its image rows (the files are swept separately).
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<Image id={self.id} filename={self.filename}>"
