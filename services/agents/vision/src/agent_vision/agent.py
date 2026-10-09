"""
Vision agent — multimodal image analysis, OCR, and diagram→code.

Like the RAG agent (and unlike the LLM-only Code/Summarizer agents), this agent
overrides `run`: it accepts an image referenced in the turn text — either a stored
reference ``<Image at db.images.{id}>`` (minted by the gateway's ``POST /images``
when the user attaches an image, resolved here via ``GET /images/{id}``) or a
direct source (a data URI, an http(s) image URL, or a local image path) — builds a
multimodal message, and streams a vision-capable model's response.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from agent_common.base import BaseAgent
from agent_common.config import get_agent_settings
from agent_common.llm import LLMConfig, build_chat_model
from agent_common.scope import current_scope
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from agent_vision.images import (
    build_multimodal_content,
    bytes_to_data_uri,
    extract_image_refs,
    extract_image_sources,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# Resolves a stored-image reference id to (bytes, mime). Injectable for tests;
# the default fetches the bytes from the gateway's GET /images/{id}.
ImageFetcher = Callable[[str], Awaitable[tuple[bytes, str]]]

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

    def __init__(
        self,
        *,
        chat_model: BaseChatModel | None = None,
        image_fetcher: ImageFetcher | None = None,
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("url", "http://localhost:8104")
        # Default to a multimodal model unless the caller injected an explicit config.
        if "llm_config" not in kwargs:
            kwargs["llm_config"] = vision_llm_config()
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._chat_model = chat_model
        self._image_fetcher = image_fetcher

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    async def _fetch_image(self, image_id: str) -> tuple[bytes, str]:
        """Resolve a stored-image reference to (bytes, mime) via the gateway.

        Fetched on behalf of the requesting user (service token + their id), so
        the gateway only serves images that user owns.
        """
        if self._image_fetcher is not None:
            return await self._image_fetcher(image_id)
        import httpx

        req = current_scope()
        if req is None:
            raise PermissionError("no request scope; refusing to fetch images")
        settings = get_agent_settings()
        base = settings.gateway_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.a2a_auth_token}",
            "X-On-Behalf-Of": str(req.user_id),
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.get(f"{base}/images/{image_id}")
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "image/png").split(";")[0].strip()
            return resp.content, mime or "image/png"

    async def _resolve_refs(self, ids: list[str]) -> list[str]:
        """Turn stored-image reference ids into inline data URIs (best-effort)."""
        resolved: list[str] = []
        for image_id in ids:
            try:
                data, mime = await self._fetch_image(image_id)
                resolved.append(bytes_to_data_uri(data, mime))
            except Exception:  # a missing/unreachable image shouldn't abort the turn
                logger.warning("vision: failed to resolve image ref %s", image_id,
                               exc_info=True)
        return resolved

    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        # Two image sources reach the agent: direct (data URI / URL / local path,
        # legacy + typed inputs) and stored-image references the gateway minted for
        # attachments. Resolve both for the *current* turn's text only.
        images, text = extract_image_sources(user_input)
        ref_ids, text = extract_image_refs(text)
        images = [*images, *await self._resolve_refs(ref_ids)]
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
