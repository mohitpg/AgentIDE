"""Summarizer agent: SKILL.md loading, A2A card shape, LLM streaming."""

from __future__ import annotations

import pytest
from agent_common import base as base_mod
from agent_common.llm import LLMConfig
from agent_summarizer.agent import SummarizerAgent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


def _cfg():
    return LLMConfig(api_key="test", model="fake")


@pytest.fixture
def summarizer(monkeypatch):
    monkeypatch.setattr(
        base_mod,
        "build_chat_model",
        lambda cfg, streaming=True: GenericFakeChatModel(
            messages=iter([AIMessage(content="a concise summary")])
        ),
    )
    return SummarizerAgent(llm_config=_cfg())


def test_loads_three_skills_from_markdown():
    agent = SummarizerAgent(llm_config=_cfg())
    assert set(agent.skills) == {"summarize-file", "summarize-thread", "tl;dr"}
    # the Markdown body became the skill prompt
    assert agent.get_skill("tl;dr").prompt.strip() != ""


def test_card_advertises_skills_and_streaming():
    card = SummarizerAgent(llm_config=_cfg()).card
    assert card.name == "Summarizer"
    assert card.capabilities.streaming is True
    assert {s.id for s in card.skills} == {"summarize-file", "summarize-thread", "tl;dr"}


@pytest.mark.asyncio
async def test_run_streams_text_chunks(summarizer):
    chunks = [c async for c in summarizer.run("tl;dr", "some long text", run_id="r1")]
    assert "".join(chunks) == "a concise summary"


@pytest.mark.asyncio
async def test_unknown_skill_falls_back_to_default(summarizer):
    chunks = [c async for c in summarizer.run("does-not-exist", "x", run_id="r1")]
    assert "".join(chunks) == "a concise summary"
