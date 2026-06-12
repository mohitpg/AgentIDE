"""SKILL.md parsing: frontmatter → AgentSkill, body → prompt."""

from __future__ import annotations

import pytest
from agent_common.skills import load_skill_file, load_skills

SKILL_MD = """---
id: my-skill
name: My Skill
description: Does a thing.
tags: [alpha, beta]
examples:
  - "do the thing"
streaming: true
---
This is the system prompt body.
It can span multiple lines.
"""


def test_parses_frontmatter_and_body(tmp_path):
    p = tmp_path / "my-skill.md"
    p.write_text(SKILL_MD, encoding="utf-8")
    skill = load_skill_file(p)

    assert skill.id == "my-skill"
    assert skill.name == "My Skill"
    assert skill.tags == ["alpha", "beta"]
    assert skill.examples == ["do the thing"]
    assert skill.prompt.startswith("This is the system prompt body.")


def test_to_agent_skill_maps_fields(tmp_path):
    p = tmp_path / "my-skill.md"
    p.write_text(SKILL_MD, encoding="utf-8")
    a2a_skill = load_skill_file(p).to_agent_skill()

    assert a2a_skill.id == "my-skill"
    assert a2a_skill.tags == ["alpha", "beta"]


def test_missing_frontmatter_raises(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        load_skill_file(p)


def test_empty_directory_raises(tmp_path):
    with pytest.raises(ValueError, match="no SKILL.md"):
        load_skills(tmp_path)
