"""Pydantic DTOs for the /rag router (Phase 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RagDocumentRead(BaseModel):
    """A document in the corpus (list/upload response)."""

    id: uuid.UUID
    filename: str
    content_type: str | None = None
    source: str
    session_id: uuid.UUID | None = None
    num_chunks: int
    byte_size: int | None = None
    created_at: datetime


class IngestResponse(BaseModel):
    """Result of indexing an uploaded document."""

    document_id: uuid.UUID
    filename: str
    num_chunks: int


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)


class RagSearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    score: float
    sources: list[str]  # which retrievers found it: "vector" / "keyword"


class RagSearchResponse(BaseModel):
    query: str
    hits: list[RagSearchHit]
