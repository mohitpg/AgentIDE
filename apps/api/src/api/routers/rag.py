"""
/rag — document ingestion + hybrid retrieval.

  POST   /rag/documents          upload → chunk → embed → store (synchronous)
  POST   /rag/documents/async     upload → enqueue Celery indexer → return a Task
  GET    /rag/documents          list indexed documents (newest first)
  DELETE /rag/documents/{id}     delete a document and its chunks
  POST   /rag/search             hybrid search (HNSW vector + full-text/BM25)
                                 → cross-encoder rerank → top-k chunks

Two ingestion paths share the same `RagPipeline`: the synchronous endpoint
indexes inline (immediately searchable, simplest), while `/documents/async`
stages the file and hands indexing to the `indexing` Celery queue so large
documents don't block the request — progress streams over `/tasks/ws` and the
final result (document_id, num_chunks) lands on the Task record. The heavy
embedder/reranker models load lazily on first use and are cached process-wide.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

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

from api.config import Settings, get_settings
from api.db.session import get_db
from api.models.conversation import Conversation
from api.models.task import Task
from api.schemas.rag import (
    IngestResponse,
    RagDocumentRead,
    RagSearchHit,
    RagSearchRequest,
    RagSearchResponse,
)
from api.schemas.tasks import TaskRead
from api.services.celery_client import enqueue

logger = logging.getLogger(__name__)
router = APIRouter()


def _upload_dir(settings: Settings) -> Path:
    """Where uploaded bytes are staged for the Celery indexer to read."""
    base = (
        Path(settings.rag_upload_dir)
        if settings.rag_upload_dir
        else Path(tempfile.gettempdir()) / "agentide_uploads"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "document"


async def _resolve_session(
    db: AsyncSession, session_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Keep the optional chat-session link only if that conversation exists."""
    if session_id is None:
        return None
    exists = await db.get(Conversation, session_id)
    return session_id if exists is not None else None


def _validate_upload(data: bytes, settings: Settings) -> None:
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.rag_max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.rag_max_upload_bytes} byte limit",
        )


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
    _validate_upload(data, get_settings())
    session_id = await _resolve_session(db, session_id)

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


@router.post(
    "/documents/async", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED
)
async def upload_document_async(
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> Task:
    """
    Stage the upload and hand indexing to the Celery `indexing` queue.

    Returns the `tasks` row immediately (202); progress + the final result
    (document_id, num_chunks) stream over `/tasks/ws` and persist on the row,
    so large documents never block the request. Same `RagPipeline` runs in the
    worker as in the synchronous endpoint.
    """
    settings = get_settings()
    data = await file.read()
    _validate_upload(data, settings)
    session_id = await _resolve_session(db, session_id)

    filename = file.filename or "document"
    task = Task(
        type="index_document",
        label=f"Index {filename}",
        queue="indexing",
        status="pending",
        params={
            "filename": filename,
            "content_type": file.content_type,
            "byte_size": len(data),
        },
        session_id=session_id,
    )
    db.add(task)
    # Commit before dispatch so the worker's first progress event finds the row.
    await db.commit()
    await db.refresh(task)

    # Stage the bytes where the worker can read them, keyed by the task id.
    path = _upload_dir(settings) / f"{task.id}__{_safe_name(filename)}"
    path.write_bytes(data)

    kwargs = {
        "document_path": str(path),
        "filename": filename,
        "content_type": file.content_type,
        "session_id": str(session_id) if session_id else None,
    }
    try:
        await asyncio.to_thread(
            enqueue,
            "workers.tasks.indexing.index_document",
            task_id=str(task.id),
            queue="indexing",
            kwargs=kwargs,
        )
    except Exception as exc:  # broker down — reflect the dispatch failure on the row
        logger.exception("failed to enqueue index_document task %s", task.id)
        task.status = "failure"
        task.error = f"dispatch failed: {exc}"
        await db.commit()
        await db.refresh(task)
        with contextlib.suppress(OSError):
            path.unlink()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Failed to enqueue indexing task"
        ) from exc

    return task


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
