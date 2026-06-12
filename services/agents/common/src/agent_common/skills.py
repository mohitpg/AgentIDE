"""
SKILL.md loader.

A skill is a Markdown file with YAML frontmatter + a body:

    ---
    id: summarize-file
    name: Summarize File
    description: Summarize the contents of a single file.
    tags: [file, summarization]
    examples:
      - "Summarize main.py"
    streaming: true
    input_modes: [text/plain]
    output_modes: [text/plain]
    ---
    You are summarizing a file. Produce a faithful, concise summary ...

The frontmatter is converted to an A2A `AgentSkill` (advertised on the Agent
Card); the body becomes the skill's system prompt at execution time. Skills are
data, not code — adding a skill means adding a `.md` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from a2a.types import AgentSkill

_FRONTMATTER_RE = "---"


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    prompt: str  # the Markdown body — used as the skill's system prompt
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    streaming: bool = True
    input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = field(default_factory=lambda: ["text/plain"])

    def to_agent_skill(self) -> AgentSkill:
        return AgentSkill(
            id=self.id,
            name=self.name,
            description=self.description,
            tags=self.tags,
            examples=self.examples or None,
            input_modes=self.input_modes,
            output_modes=self.output_modes,
        )


def _parse_frontmatter(text: str, source: str) -> tuple[dict, str]:
    """Split `---\\n<yaml>\\n---\\n<body>` into (metadata, body)."""
    stripped = text.lstrip("﻿").lstrip()
    if not stripped.startswith(_FRONTMATTER_RE):
        raise ValueError(f"{source}: missing YAML frontmatter (must start with '---')")
    # Drop the leading '---', then split on the closing '---'.
    _, _, rest = stripped.partition("\n")
    front, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError(f"{source}: unterminated frontmatter (no closing '---')")
    meta = yaml.safe_load(front) or {}
    # body begins after the closing '---' line
    body = body.partition("\n")[2] if "\n" in body else ""
    return meta, body.strip()


def load_skill_file(path: Path) -> SkillDefinition:
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"), path.name)
    skill_id = meta.get("id") or path.stem
    missing = [k for k in ("name", "description") if not meta.get(k)]
    if missing:
        raise ValueError(f"{path.name}: frontmatter missing required keys: {missing}")
    if not body:
        raise ValueError(f"{path.name}: empty body (the body is the skill's prompt)")
    return SkillDefinition(
        id=skill_id,
        name=meta["name"],
        description=meta["description"],
        prompt=body,
        tags=list(meta.get("tags") or []),
        examples=list(meta.get("examples") or []),
        streaming=bool(meta.get("streaming", True)),
        input_modes=list(meta.get("input_modes") or ["text/plain"]),
        output_modes=list(meta.get("output_modes") or ["text/plain"]),
    )


def load_skills(directory: Path) -> dict[str, SkillDefinition]:
    """Load every `*.md` skill in a directory, keyed by skill id (sorted)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"skills directory not found: {directory}")
    skills: dict[str, SkillDefinition] = {}
    for path in sorted(directory.glob("*.md")):
        skill = load_skill_file(path)
        skills[skill.id] = skill
    if not skills:
        raise ValueError(f"no SKILL.md files found in {directory}")
    return skills
