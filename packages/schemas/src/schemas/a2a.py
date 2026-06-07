"""
A2A (Agent-to-Agent) protocol types.

Each specialist agent exposes an AgentCard describing its skills.
The supervisor reads cards from the registry and routes accordingly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class SkillInput(BaseModel):
    """JSON Schema-lite description of a skill's input."""

    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class SkillOutput(BaseModel):
    """JSON Schema-lite description of a skill's output."""

    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    """One capability an agent exposes."""

    id: str  # e.g. "summarize-file"
    name: str  # human-readable
    description: str
    input_schema: SkillInput = Field(default_factory=SkillInput)
    output_schema: SkillOutput = Field(default_factory=SkillOutput)
    streaming: bool = False  # agent streams tokens for this skill
    tags: list[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """
    The A2A Agent Card — the contract an agent publishes at /.well-known/agent.json.
    The registry indexes these so the supervisor can discover skills.
    """

    id: str  # unique agent id, e.g. "summarizer"
    name: str
    description: str
    version: str = "0.1.0"
    url: HttpUrl  # base URL for A2A calls
    skills: list[Skill]
    auth: Literal["none", "bearer"] = "bearer"
    tags: list[str] = Field(default_factory=list)
