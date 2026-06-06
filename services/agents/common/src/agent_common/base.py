"""
BaseAgent – abstract base every specialist agent inherits.

Subclasses implement `execute_skill` and declare their AgentCard.
The A2A server (create_agent_app) wires HTTP routes automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from schemas.a2a import AgentCard, Skill


class BaseAgent(ABC):
    """Inherit this and implement `card` + `execute_skill`."""

    @property
    @abstractmethod
    def card(self) -> AgentCard:
        """Return this agent's A2A Agent Card (skills, URL, etc.)."""
        ...

    @abstractmethod
    async def execute_skill(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        """
        Execute a skill synchronously and return the result.
        For streaming skills override `stream_skill` instead.
        """
        ...

    async def stream_skill(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        run_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute a skill and yield streaming events.
        Default: calls execute_skill and yields a single done event.
        Override for real token streaming.
        """
        result = await self.execute_skill(skill_id, input_data, run_id)
        yield {"type": "done", "data": result}

    def get_skill(self, skill_id: str) -> Skill | None:
        return next((s for s in self.card.skills if s.id == skill_id), None)
