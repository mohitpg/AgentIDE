"""Web-crawl tasks – bulk page fetching and extraction. Implemented in Phase 5."""

from workers.app import celery_app


@celery_app.task(name="workers.tasks.web.crawl_urls", bind=True)
def crawl_urls(self, urls: list[str], session_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Fetch + extract text from a list of URLs. Phase 5."""
    self.update_state(state="PROGRESS", meta={"progress": 0, "message": "Queued"})
    return {"status": "not_implemented", "url_count": len(urls)}
