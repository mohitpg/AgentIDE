"""
Agent directory — maps an agent id to its A2A base URL.

This is the *static* precursor to the Phase-2 Postgres+Redis agent registry: the
supervisor looks up where to send a delegated skill call. Swapping this for the
registry later only changes how the map is populated, not how the graph uses it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Defaults match the ports the agent `app.py` entry points listen on.
_DEFAULTS = {
    "summarizer": "http://localhost:8101",
    "code": "http://localhost:8102",
    "rag": "http://localhost:8103",
    "vision": "http://localhost:8104",
    "web": "http://localhost:8105",
}


@dataclass(frozen=True)
class AgentDirectory:
    urls: dict[str, str] = field(default_factory=lambda: dict(_DEFAULTS))

    @classmethod
    def from_env(cls) -> AgentDirectory:
        """Override defaults via SUMMARIZER_AGENT_URL / CODE_AGENT_URL env vars."""
        urls = dict(_DEFAULTS)
        for agent_id in urls:
            env_key = f"{agent_id.upper()}_AGENT_URL"
            if (override := os.environ.get(env_key)):
                urls[agent_id] = override
        return cls(urls=urls)

    def url_for(self, agent_id: str) -> str | None:
        return self.urls.get(agent_id)

    def known_agents(self) -> list[str]:
        return list(self.urls)
