"""drop mcp_servers table

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19 00:00:00.000000

MCP servers now run on each user's machine, inside the desktop app, and reach
the cloud agents through the gateway's bridge relay (`/mcp/bridge/ws`). Nothing
reads the host-wide registry any more. Downgrade recreates it empty.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("mcp_servers")


def downgrade() -> None:
    # Same shape as 0006.
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("transport", sa.String(16), nullable=False, server_default="stdio"),
        sa.Column("command", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "args", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "env", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("cwd", sa.String(512), nullable=True),
        sa.Column("url", sa.String(512), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "allowed_agents",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tool_allowlist",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
