"""
MCP server registry model

One row per registered MCP server. This is the settings-driven plug-in surface:
operators add a server (filesystem, git, web-fetch, or any 3rd-party MCP server)
and assign it to agents from the UI — no code changes. The columns mirror
`mcp_manager.MCPServerConfig` so a row maps straight onto a connectable config.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin

_JSON = JSON().with_variant(JSONB, "postgresql")


class MCPServer(Base, TimestampMixin):
    __tablename__ = "mcp_servers"

    # Stable id
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(String(16), nullable=False, default="stdio")

    # stdio transport
    command: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    args: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    env: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    cwd: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # sse transport
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # exposure
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_agents: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    tool_allowlist: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)

    def __repr__(self) -> str:
        return f"<MCPServer id={self.id} transport={self.transport} enabled={self.enabled}>"
