"""
Vision agent tests — no model, no network, no real image files.

Card/skills are exercised directly; `run` is exercised with an injected fake chat
model and a data-URI image, so neither a multimodal model nor any file I/O is
needed. The fake model records the messages it received so we can assert the
multimodal message shape.
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from agent_vision.agent import VisionAgent

_IMG = "data:image/png;base64,AAAABBBBCCCC"


class FakeChatModel:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces
        self.last_messages: list | None = None

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        self.last_messages = messages
        for p in self._pieces:
            yield AIMessageChunk(content=p)


async def _collect(agent: VisionAgent, skill: str, text: str) -> str:
    return "".join([chunk async for chunk in agent.run(skill, text)])


def test_card_and_skills_load() -> None:
    agent = VisionAgent(chat_model=FakeChatModel([]))
    assert agent.id == "vision"
    assert agent.card.name == "Vision"
    assert set(agent.skills) == {"analyze-image", "ocr", "diagram-to-code"}


def test_default_skill_is_analyze_image() -> None:
    agent = VisionAgent(chat_model=FakeChatModel([]))
    assert agent.default_skill_id() == "analyze-image"


def test_card_advertises_image_input_modes() -> None:
    skills = {s.id: s for s in VisionAgent(chat_model=FakeChatModel([])).card.skills}
    assert "image/png" in skills["analyze-image"].input_modes


async def test_analyze_streams_text_for_image() -> None:
    agent = VisionAgent(chat_model=FakeChatModel(["A cat ", "on a mat."]))
    out = await _collect(agent, "analyze-image", f"What is this? {_IMG}")
    assert out == "A cat on a mat."


async def test_no_image_returns_guidance() -> None:
    agent = VisionAgent(chat_model=FakeChatModel(["unused"]))
    out = await _collect(agent, "analyze-image", "just text, no image attached")
    assert "didn't find an image" in out


async def test_builds_multimodal_message_with_image_and_skill_prompt() -> None:
    model = FakeChatModel(["ok"])
    await _collect(VisionAgent(chat_model=model), "ocr", f"read this {_IMG}")

    system, human = model.last_messages  # [SystemMessage, HumanMessage]
    blocks = human.content
    assert isinstance(blocks, list)
    assert {"type": "image_url", "image_url": {"url": _IMG}} in blocks
    text_block = next(b for b in blocks if b.get("type") == "text")
    assert "read this" in text_block["text"]
    # the system prompt is the ocr skill body
    assert "transcribe" in system.content.lower()


async def test_image_only_uses_skill_default_prompt() -> None:
    model = FakeChatModel(["x"])
    await _collect(VisionAgent(chat_model=model), "diagram-to-code", _IMG)

    human = model.last_messages[1]
    text_block = next(b for b in human.content if b.get("type") == "text")
    assert "code" in text_block["text"].lower()
