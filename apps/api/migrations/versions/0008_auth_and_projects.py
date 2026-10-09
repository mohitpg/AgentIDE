"""auth + per-user/project scoping

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 00:00:00.000000

  users           — Google sign-in: `google_sub` (stable account id), `avatar_url`;
                    `hashed_password` becomes nullable (OAuth-only accounts).
  projects        — the isolation boundary inside an account.
  refresh_tokens  — hashed, rotating refresh tokens.
  conversations / tasks / images / rag_documents
                  — gain `user_id` + `project_id` owner columns. Every query
                    filters on both. Pre-existing rows stay NULL (invisible) until
                    the first user signs in and adopts them into their default
                    project (see `api.services.users`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that gain owner columns. conversations already has user_id (0002).
_SCOPED = ("conversations", "tasks", "images", "rag_documents")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=True)
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)

    # ── projects ─────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    # ── refresh_tokens ───────────────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )

    # ── owner columns on user data ───────────────────────────────────────────
    for table in _SCOPED:
        if table != "conversations":
            op.add_column(table, sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
            op.create_foreign_key(
                f"fk_{table}_user_id", table, "users", ["user_id"], ["id"], ondelete="CASCADE"
            )
            op.create_index(f"ix_{table}_user_id", table, ["user_id"])
        op.add_column(table, sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_project_id", table, "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    for table in reversed(_SCOPED):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_column(table, "project_id")
        if table != "conversations":
            op.drop_index(f"ix_{table}_user_id", table_name=table)
            op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
            op.drop_column(table, "user_id")

    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")

    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "google_sub")
    # Google-only accounts have no password. Give them an unusable hash rather
    # than deleting them (deleting would cascade to their conversations).
    op.execute("UPDATE users SET hashed_password = '!' WHERE hashed_password IS NULL")
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=False)
