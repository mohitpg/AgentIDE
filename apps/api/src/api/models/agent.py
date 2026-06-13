"""
Agent registry model – Phase 2.

One row per registered specialist agent. Stores the full A2A Agent Card (the
document served at /.well-known/agent-card.json) plus a few denormalized columns
the registry and supervisor query on the hot path:

  - `url`        – where to send delegated A2A calls,
  - `skill_ids`  – for skill-based discovery,
  - `tags`       – for tag-based discovery,
  - `last_heartbeat` – liveness; stale rows are filtered from discovery and reaped.

The agent's stable id (``"code"``, ``"summarizer"``) is the natural primary key —
it is *not* part of the A2A card (which identifies by name + url), so agents send
it alongside the card when they register.

Column types use a portable JSON variant (JSONB on Postgres) so the table can be
created on SQLite in unit tests without a live database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin

# JSONB on Postgres (indexable, typed); plain JSON elsewhere (e.g. SQLite tests).
_JSON = JSON().with_variant(JSONB, "postgresql")


class AgentRegistration(Base, TimestampMixin):
    __tablename__ = "agent_registrations"

    # Stable agent id, e.g. "code" / "summarizer" — natural primary key.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")

    # Full A2A AgentCard as published by the agent.
    card: Mapped[dict] = mapped_column(_JSON, nullable=False)
    # Denormalized from `card` for cheap discovery filters.
    skill_ids: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)

    # Liveness: agents heartbeat periodically; a row older than the registry TTL
    # is considered down.
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentRegistration id={self.id} url={self.url}>"
