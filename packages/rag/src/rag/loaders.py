"""
Document loaders — turn raw uploaded bytes into plain text.

Base support:
  * PDF  → extracted page-by-page with `pypdf`
  * everything else (txt, md, source code, …) → decoded as UTF-8 (lossy)
"""

from __future__ import annotations

import io


class UnsupportedDocumentError(ValueError):
    """Raised when a document cannot be turned into text."""


def _is_pdf(filename: str, content_type: str | None) -> bool:
    return filename.lower().endswith(".pdf") or (content_type or "").lower() == "application/pdf"


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise UnsupportedDocumentError(
            "PDF support requires the 'pypdf' package to be installed"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def extract_text(filename: str, content_type: str | None, data: bytes) -> str:
    """
    Best-effort text extraction. Returns the document's text content.

    Raises `UnsupportedDocumentError` if the bytes can't be decoded at all.
    """
    if _is_pdf(filename, content_type):
        text = _extract_pdf(data)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")

    text = text.strip()
    if not text:
        raise UnsupportedDocumentError(f"No extractable text found in {filename!r}")
    return text
