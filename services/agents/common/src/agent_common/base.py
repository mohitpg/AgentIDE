"""
BaseAgent — declarative base every specialist agent inherits.

A subclass declares identity (`id`, `name`, `description`) and a `skills_dir`
containing SKILL.md files. The base:
  - loads the skills (frontmatter → AgentSkill, body → system prompt),
  - builds the A2A `AgentCard`,
  - provides a default LLM-streaming `run()` that uses the skill's body as the
    system prompt.

Tool-using agents (e.g. Code with filesystem/git MCP in Phase 6) override `run`.
The generic `LLMAgentExecutor` in `executor.py` adapts any BaseAgent to the A2A
protocol, so agents never touch protocol plumbing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from a2a.types import AgentCapabilities, AgentCard
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from agent_common.config import get_agent_settings
from agent_common.llm import LLMConfig, build_chat_model
from agent_common.skills import SkillDefinition, load_skills


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


class BaseAgent(ABC):
    # ── identity (override in subclass) ────────────────────────────────────────
    id: str
    name: str
    description: str
    version: str = "0.1.0"

    def __init__(self, *, llm_config: LLMConfig | None = None, url: str | None = None) -> None:
        self._llm_config = llm_config or LLMConfig.from_settings(get_agent_settings())
        self._url = url or "http://localhost:8000"
        self._skills: dict[str, SkillDefinition] = load_skills(self.skills_dir)

    # ── skills ─────────────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def skills_dir(self) -> Path:
        """Directory holding this agent's SKILL.md files."""
        ...

    @property
    def skills(self) -> dict[str, SkillDefinition]:
        return self._skills

    def get_skill(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def default_skill_id(self) -> str:
        return next(iter(self._skills))

    # ── lifecycle (override for tool/resource setup) ───────────────────────────
    async def astartup(self) -> None:
        """Called once when the A2A app starts. Default: nothing.

        Tool-using agents override this to connect their MCP servers (Phase 6).
        """

    async def ashutdown(self) -> None:
        """Called once when the A2A app stops. Mirror of `astartup`."""

    # ── A2A Agent Card ─────────────────────────────────────────────────────────
    @property
    def card(self) -> AgentCard:
        streaming = any(s.streaming for s in self._skills.values())
        return AgentCard(
            name=self.name,
            description=self.description,
            url=self._url,
            version=self.version,
            capabilities=AgentCapabilities(streaming=streaming),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[s.to_agent_skill() for s in self._skills.values()],
        )

    # ── execution (override for tool use) ──────────────────────────────────────
    async def run(
        self,
        skill_id: str,
        user_input: str,
        *,
        run_id: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream the skill's response as text chunks. Default implementation runs
        the LLM with the skill's Markdown body as the system prompt.
        """
        skill = self.get_skill(skill_id) or self._skills[self.default_skill_id()]
        model = build_chat_model(self._llm_config, streaming=True)
        messages = [
            SystemMessage(content=skill.prompt),
            HumanMessage(content=user_input),
        ]
        async for chunk in model.astream(messages):
            if isinstance(chunk, AIMessageChunk):
                piece = _text(chunk.content)
                if piece:
                    yield piece
