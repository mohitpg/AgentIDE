"""
Async task record

One row per job dispatched to the Celery workers (RabbitMQ broker, Redis result
backend). The gateway creates the row (`status="pending"`) and enqueues the job
with `task_id = row.id`, so the row id, the Celery task id, and every progress
event's `task_id` are the same value.

"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin

# JSONB on Postgres (indexable, typed); plain JSON elsewhere (e.g. SQLite tests).
_JSON = JSON().with_variant(JSONB, "postgresql")

# Lifecycle states. Anything not in TERMINAL_STATUSES is considered "active".
TERMINAL_STATUSES = frozenset({"success", "failure", "revoked"})


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    # Row id == Celery task id == every progress event's task_id.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Logical task type (e.g. "mock", "index_repository"); maps to a Celery task.
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, default="default")

    # pending | started | progress | success | failure | revoked
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional link to the chat session that kicked the task off.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def __repr__(self) -> str:
        return f"<Task id={self.id} type={self.type} status={self.status}>"
