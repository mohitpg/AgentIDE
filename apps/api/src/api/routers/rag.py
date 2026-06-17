"""
/rag — document ingestion + hybrid retrieval (Phase 4).

  POST   /rag/documents          upload a file → chunk → embed → store in pgvector
  GET    /rag/documents          list indexed documents (newest first)
  DELETE /rag/documents/{id}     delete a document and its chunks
  POST   /rag/search             hybrid search (HNSW vector + full-text/BM25)
                                 → cross-encoder rerank → top-k chunks

Ingestion is synchronous in this base pass so an upload is immediately
searchable; the same `RagPipeline` is reusable from the Celery indexer for
large/async corpora later. The heavy embedder/reranker models load lazily on
first use and are cached process-wide by `packages/rag`.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from rag import RagConfig, RagPipeline, UnsupportedDocumentError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db.session import get_db
from api.schemas.rag import (
    IngestResponse,
    RagDocumentRead,
    RagSearchHit,
    RagSearchRequest,
    RagSearchResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@lru_cache
def _pipeline() -> RagPipeline:
    """One process-wide pipeline; underlying models are cached in packages/rag."""
    s = get_settings()
    return RagPipeline(
        RagConfig(
            embedding_model=s.rag_embedding_model,
            embedding_dim=s.rag_embedding_dim,
            reranker_model=s.rag_reranker_model,
            chunk_tokens=s.rag_chunk_tokens,
            chunk_overlap=s.rag_chunk_overlap,
            candidate_k=s.rag_candidate_k,
            top_k=s.rag_top_k,
        )
    )


def get_pipeline() -> RagPipeline:
    return _pipeline()


@router.post("/documents", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> IngestResponse:
    data = await file.read()
    settings = get_settings()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.rag_max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.rag_max_upload_bytes} byte limit",
        )

    try:
        result = await pipeline.ingest(
            db,
            filename=file.filename or "document",
            content_type=file.content_type,
            data=data,
            session_id=session_id,
        )
    except UnsupportedDocumentError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return IngestResponse(
        document_id=result.document_id,
        filename=result.filename,
        num_chunks=result.num_chunks,
    )


@router.get("/documents", response_model=list[RagDocumentRead])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> list[RagDocumentRead]:
    rows = await pipeline.store.list_documents(db)
    return [RagDocumentRead(**row) for row in rows]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> None:
    deleted = await pipeline.store.delete_document(db, document_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")


@router.post("/search", response_model=RagSearchResponse)
async def search(
    payload: RagSearchRequest,
    db: AsyncSession = Depends(get_db),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> RagSearchResponse:
    hits = await pipeline.search(
        db,
        payload.query,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k,
    )
    return RagSearchResponse(
        query=payload.query,
        hits=[
            RagSearchHit(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                filename=h.filename,
                chunk_index=h.chunk_index,
                content=h.content,
                score=h.score,
                sources=h.sources,
            )
            for h in hits
        ],
    )
