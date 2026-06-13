"""Pydantic DTOs for the /agents registry API (Phase 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    """An agent self-registering: its stable id + its A2A Agent Card."""

    agent_id: str = Field(min_length=1, max_length=64)
    card: dict[str, Any]
    # Publicly reachable base URL, if it differs from the card's `url`.
    url: str | None = None


class AgentInfo(BaseModel):
    """A registered agent as returned by list / discover."""

    agent_id: str
    name: str
    description: str | None = None
    url: str
    version: str
    skills: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    last_heartbeat: datetime
    card: dict[str, Any]

    @classmethod
    def from_record(cls, rec: dict) -> AgentInfo:
        return cls(
            agent_id=rec["agent_id"],
            name=rec["name"],
            description=rec.get("description"),
            url=rec["url"],
            version=rec["version"],
            skills=rec.get("skill_ids", []),
            tags=rec.get("tags", []),
            last_heartbeat=rec["last_heartbeat"],
            card=rec["card"],
        )
