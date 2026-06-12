"""Code agent: SKILL.md loading, A2A card shape, LLM streaming."""

from __future__ import annotations

import pytest
from agent_code.agent import CodeAgent
from agent_common import base as base_mod
from agent_common.llm import LLMConfig
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


def _cfg():
    return LLMConfig(api_key="test", model="fake")


@pytest.fixture
def code(monkeypatch):
    monkeypatch.setattr(
        base_mod,
        "build_chat_model",
        lambda cfg, streaming=True: GenericFakeChatModel(
            messages=iter([AIMessage(content="def f(): pass")])
        ),
    )
    return CodeAgent(llm_config=_cfg())


def test_loads_three_skills_from_markdown():
    agent = CodeAgent(llm_config=_cfg())
    assert set(agent.skills) == {"generate", "edit", "explain"}


def test_card_shape():
    card = CodeAgent(llm_config=_cfg()).card
    assert card.name == "Code"
    assert {s.id for s in card.skills} == {"generate", "edit", "explain"}


@pytest.mark.asyncio
async def test_run_generate_streams(code):
    chunks = [c async for c in code.run("generate", "a function", run_id="r1")]
    assert "".join(chunks) == "def f(): pass"
