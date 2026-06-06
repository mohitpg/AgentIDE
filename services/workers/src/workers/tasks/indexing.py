"""
Indexing tasks – load → chunk → embed → upsert into pgvector.
Implemented in Phase 4.
"""

from workers.app import celery_app


@celery_app.task(name="workers.tasks.indexing.index_repository", bind=True)
def index_repository(self, project_id: str, repo_path: str) -> dict:  # type: ignore[no-untyped-def]
    """Index an entire repository into pgvector. Phase 4."""
    # TODO(phase-4): implement chunking → embedding → pgvector upsert
    self.update_state(state="PROGRESS", meta={"progress": 0, "message": "Queued"})
    return {"status": "not_implemented", "project_id": project_id}


@celery_app.task(name="workers.tasks.indexing.index_document", bind=True)
def index_document(self, project_id: str, document_path: str) -> dict:  # type: ignore[no-untyped-def]
    """Index a single file/document. Phase 4."""
    self.update_state(state="PROGRESS", meta={"progress": 0, "message": "Queued"})
    return {"status": "not_implemented", "document_path": document_path}
