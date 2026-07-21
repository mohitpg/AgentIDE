"""
Summarizer agent.

"""

from __future__ import annotations

from pathlib import Path

from agent_common.base import BaseAgent

_SKILLS_DIR = Path(__file__).parent / "skills"


class SummarizerAgent(BaseAgent):
    id = "summarizer"
    name = "Summarizer"
    description = "Condenses files, threads, and arbitrary text into faithful summaries."

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("url", "http://localhost:8101")
        super().__init__(**kwargs)

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR
