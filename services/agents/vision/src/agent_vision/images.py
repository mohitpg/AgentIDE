"""
Image input handling for the Vision agent.

The chat / A2A transport carries a single text field, so images reach the agent
embedded in that text as one of:
  - a data URI            ``data:image/png;base64,iVBORw0...``
  - an http(s) image URL  ``https://example.com/diagram.png``
  - a local file path      ``C:\\shots\\ui.png`` / ``/home/me/diagram.jpg``

`extract_image_sources` pulls these out (local paths are read and inlined as data
URIs, so the model call never needs filesystem access) and returns the residual
text to use as the prompt. The model receives ``image_url`` content blocks — the
OpenAI / OpenRouter standard multimodal format — built by `build_multimodal_content`.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# A base64 data URI for an image, up to the first whitespace.
_DATA_URI_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")
# An http(s) URL whose path ends in a known image extension (optional query string).
_HTTP_IMG_RE = re.compile(
    r"https?://\S+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?\S*)?",
    re.IGNORECASE,
)


def bytes_to_data_uri(data: bytes, mime: str = "image/png") -> str:
    """Encode raw image bytes as a base64 data URI."""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def path_to_data_uri(path: str | Path) -> str:
    """Read a local image file and inline it as a base64 data URI."""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return bytes_to_data_uri(p.read_bytes(), mime)


def _looks_like_image_path(token: str) -> bool:
    return Path(token).suffix.lower() in IMAGE_EXTENSIONS


def extract_image_sources(text: str) -> tuple[list[str], str]:
    """Split ``text`` into (image sources, residual prompt text).

    Image sources are returned as values ready to drop into an ``image_url`` block:
    data URIs and http(s) URLs pass through unchanged; local image paths are read
    and inlined as data URIs. The residual text has the matched tokens removed and
    is whitespace-normalised, so it can be used directly as the prompt.
    """
    sources: list[str] = []
    residual = text

    for pattern in (_DATA_URI_RE, _HTTP_IMG_RE):
        sources.extend(pattern.findall(residual))
        residual = pattern.sub(" ", residual)

    # Whitespace-separated tokens that name an existing local image file.
    remaining: list[str] = []
    for token in residual.split():
        candidate = token.strip("\"'<>()[]")
        if _looks_like_image_path(candidate) and Path(candidate).is_file():
            sources.append(path_to_data_uri(candidate))
        else:
            remaining.append(token)

    return sources, " ".join(remaining).strip()


def build_multimodal_content(prompt: str, images: list[str]) -> list[str | dict[Any, Any]]:
    """Build an OpenAI/OpenRouter multimodal message content list (text + images).

    Typed as ``list[str | dict]`` to match LangChain's message ``content`` (lists
    are invariant, so a narrower element type would not assign cleanly).
    """
    blocks: list[str | dict[Any, Any]] = [{"type": "text", "text": prompt}]
    blocks.extend({"type": "image_url", "image_url": {"url": src}} for src in images)
    return blocks
