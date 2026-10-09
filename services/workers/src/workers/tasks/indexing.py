"""
Indexing tasks — load → chunk → embed → upsert into pgvector.

`index_document` is the async ingestion path: the gateway stages an uploaded file
on disk, creates a `tasks` row, and enqueues this task with the file path. The
worker reads the bytes, runs the shared `RagPipeline.ingest` (the same code the
synchronous `/rag/documents` endpoint uses), and reports lifecycle/progress over
Redis pub/sub exactly like the mock task — so the existing TaskEventHub persists
the record and the UI Tasks panel renders live status with no new plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from workers.app import celery_app
from workers.progress import publish

# Async DB URL (asyncpg). Mirrors api.config.Settings.database_url so one .env
# drives the whole stack; the worker reads it straight from the environment.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://agentide:agentide@localhost:5433/agentide"
)

T = TypeVar("T")


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Drive an async coroutine from the sync Celery task.

    asyncpg needs a selector event loop on Windows (the default Proactor loop
    can't run it), so pin the policy before spinning up a fresh loop per task.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def _rag_config():  # type: ignore[no-untyped-def]
    from rag import RagConfig

    return RagConfig(
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", RagConfig.embedding_model),
        embedding_dim=int(os.getenv("RAG_EMBEDDING_DIM", RagConfig.embedding_dim)),
        chunk_tokens=int(os.getenv("RAG_CHUNK_TOKENS", RagConfig.chunk_tokens)),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", RagConfig.chunk_overlap)),
    )


async def _ingest_async(
    data: bytes,
    filename: str,
    content_type: str | None,
    session_id: uuid.UUID | None,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    from rag import RagPipeline, RagScope
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            pipeline = RagPipeline(_rag_config())
            result = await pipeline.ingest(
                session,
                scope=RagScope(user_id=user_id, project_id=project_id),
                filename=filename,
                content_type=content_type,
                data=data,
                source="upload",
                session_id=session_id,
            )
            await session.commit()
            return {
                "document_id": str(result.document_id),
                "filename": result.filename,
                "num_chunks": result.num_chunks,
            }
    finally:
        await engine.dispose()


@celery_app.task(name="workers.tasks.indexing.index_document", bind=True)
def index_document(  # type: ignore[no-untyped-def]
    self,
    document_path: str,
    filename: str | None = None,
    content_type: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
):
    """Index a single staged file into pgvector under the uploader's scope."""
    task_id = self.request.id
    publish(task_id, status="started", progress=0, message="Starting ingestion")
    path = Path(document_path)
    try:
        data = path.read_bytes()  # FileNotFoundError if the staged file is gone
        name = filename or path.name
        publish(
            task_id,
            status="progress",
            progress=20,
            message=f"Read {name} ({len(data)} bytes)",
        )

        if not (user_id and project_id):
            # Never index an ownerless document: no scoped search could find it.
            raise ValueError("index_document requires user_id and project_id")
        sid = uuid.UUID(session_id) if session_id else None
        publish(task_id, status="progress", progress=40, message="Chunking & embedding")
        result = _run_async(
            _ingest_async(
                data, name, content_type, sid, uuid.UUID(user_id), uuid.UUID(project_id)
            )
        )

        publish(
            task_id,
            status="success",
            progress=100,
            message=f"Indexed {result['num_chunks']} chunks",
            result=result,
        )
        return result
    except Exception as exc:  # noqa: BLE001 — surface to the UI, then re-raise
        publish(task_id, status="failure", message=str(exc), error=str(exc))
        raise
    finally:
        # The staged upload is consumed; remove it whether we succeeded or not.
        with contextlib.suppress(OSError):
            path.unlink()


@celery_app.task(name="workers.tasks.indexing.index_repository", bind=True)
def index_repository(self, project_id: str, repo_path: str) -> dict:  # type: ignore[no-untyped-def]
    """Index an entire repository into pgvector. (Walk + per-file chunking — later.)"""
    self.update_state(state="PROGRESS", meta={"progress": 0, "message": "Queued"})
    return {"status": "not_implemented", "project_id": project_id}
