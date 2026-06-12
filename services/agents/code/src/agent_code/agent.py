"""
Code agent.

Declarative: identity + a skills directory of SKILL.md files
(generate, edit, explain). Execution is the BaseAgent default LLM streaming,
using each skill's Markdown body as its system prompt. Filesystem/git MCP tools
are added by overriding `run` in Phase 6.
"""

from __future__ import annotations

from pathlib import Path

from agent_common.base import BaseAgent

_SKILLS_DIR = Path(__file__).parent / "skills"


class CodeAgent(BaseAgent):
    id = "code"
    name = "Code"
    description = "Generates, edits, and explains code."

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("url", "http://localhost:8102")
        super().__init__(**kwargs)

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR
