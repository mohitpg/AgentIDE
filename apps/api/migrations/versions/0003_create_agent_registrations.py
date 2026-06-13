"""create agent_registrations table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-12 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_registrations",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="0.1.0"),
        sa.Column("card", postgresql.JSONB(), nullable=False),
        sa.Column(
            "skill_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "last_heartbeat",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Discovery filters on liveness; index the heartbeat for stale-row scans.
    op.create_index(
        "ix_agent_registrations_last_heartbeat",
        "agent_registrations",
        ["last_heartbeat"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_registrations_last_heartbeat", table_name="agent_registrations"
    )
    op.drop_table("agent_registrations")
