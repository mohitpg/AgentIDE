"""
Vision agent — multimodal image analysis, OCR, and diagram→code.

Like the RAG agent (and unlike the LLM-only Code/Summarizer agents), this agent
overrides `run`: it accepts an image embedded in the turn text (a data URI, an
http(s) image URL, or a local image path), builds a multimodal message, and
streams a vision-capable model's response. Three skills:

  analyze-image    — describe / analyse what the image shows.
  ocr              — transcribe the text in the image verbatim.
  diagram-to-code  — turn a diagram / mockup / screenshot into code.

It is an independent A2A service. It defaults to a multimodal model (distinct from
the stack's text default) via ``OPENROUTER_VISION_MODEL``; heavy / batch jobs run
off the request path on the Celery ``vision`` queue (``workers.tasks.vision``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from agent_common.base import BaseAgent
from agent_common.config import get_agent_settings
from agent_common.llm import LLMConfig, build_chat_model
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from agent_vision.images import build_multimodal_content, extract_image_sources

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

_SKILLS_DIR = Path(__file__).parent / "skills"

_NO_IMAGE = (
    "I didn't find an image to analyze. Attach an image alongside your message — "
    "a data URI, an image URL, or a local image path — and I'll take a look."
)

# Instruction used when the user supplies an image but no prompt of their own.
_DEFAULT_PROMPTS = {
    "analyze-image": "Describe and analyze this image in detail.",
    "ocr": "Extract all text from this image, preserving its structure.",
    "diagram-to-code": "Convert this diagram into working code.",
}


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


def vision_llm_config() -> LLMConfig:
    """LLMConfig pinned to the multimodal model (OPENROUTER_VISION_MODEL)."""
    s = get_agent_settings()
    return LLMConfig(
        api_key=s.openrouter_api_key,
        model=s.openrouter_vision_model,
        max_tokens=s.llm_max_tokens,
        temperature=s.llm_temperature,
    )


class VisionAgent(BaseAgent):
    id = "vision"
    name = "Vision"
    description = "Analyzes images: visual analysis, OCR, and diagram-to-code."

    def __init__(self, *, chat_model: BaseChatModel | None = None, **kwargs: object) -> None:
        kwargs.setdefault("url", "http://localhost:8104")
        # Default to a multimodal model unless the caller injected an explicit config.
        if "llm_config" not in kwargs:
            kwargs["llm_config"] = vision_llm_config()
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._chat_model = chat_model

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        images, text = extract_image_sources(user_input)
        if not images:
            yield _NO_IMAGE
            return

        skill = self.get_skill(skill_id) or self._skills[self.default_skill_id()]
        prompt = text or _DEFAULT_PROMPTS.get(skill.id, "Describe this image.")
        model = self._chat_model or build_chat_model(self._llm_config, streaming=True)
        messages = [
            SystemMessage(content=skill.prompt),
            HumanMessage(content=build_multimodal_content(prompt, images)),
        ]
        async for chunk in model.astream(messages):
            if isinstance(chunk, AIMessageChunk):
                piece = _text(chunk.content)
                if piece:
                    yield piece
