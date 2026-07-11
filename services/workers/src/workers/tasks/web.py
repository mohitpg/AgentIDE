"""
Web-crawl tasks — bulk page fetching + extraction off the request path (Phase 5).

The Web Search agent fetches a handful of pages inline for an interactive turn. A
*heavy* crawl — many URLs, or one the caller wants to run in the background — is
enqueued here on the `web` queue instead. The task reuses the agent's own
`fetch_url_sync` / `html_to_text` (Celery is synchronous), and reports
lifecycle/progress over Redis pub/sub exactly like the indexing task, so the
existing TaskEventHub persists the record and the UI Tasks panel renders live
status with no new plumbing.

Best-effort per URL: a single dead link is recorded as an error and the crawl
continues, mirroring the graceful-degradation used everywhere else in the stack.
"""

from __future__ import annotations

from agent_web.search import fetch_url_sync

from workers.app import celery_app
from workers.progress import publish

# Per-page character cap on extracted text, so one huge page can't bloat results.
_MAX_CHARS = 4000


@celery_app.task(name="workers.tasks.web.crawl_urls", bind=True)
def crawl_urls(self, urls: list[str], session_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Fetch + extract readable text from a list of URLs, reporting progress."""
    task_id = self.request.id
    total = len(urls)
    publish(task_id, status="started", progress=0, message=f"Crawling {total} URL(s)")

    pages: list[dict] = []
    errors: list[dict] = []
    for i, url in enumerate(urls, start=1):
        try:
            text = fetch_url_sync(url, max_chars=_MAX_CHARS)
            pages.append({"url": url, "text": text, "chars": len(text)})
            message = f"Fetched {url}"
        except Exception as exc:  # noqa: BLE001 — one bad URL shouldn't fail the crawl
            errors.append({"url": url, "error": str(exc)})
            message = f"Failed {url}: {exc}"
        # Progress is spread across the URLs; 100 is reserved for the terminal event.
        publish(
            task_id,
            status="progress",
            progress=int(i / total * 100) if total else 100,
            message=message,
        )

    result = {
        "url_count": total,
        "fetched": len(pages),
        "failed": len(errors),
        "pages": pages,
        "errors": errors,
        "session_id": session_id,
    }
    publish(
        task_id,
        status="success",
        progress=100,
        message=f"Crawled {len(pages)}/{total} URL(s)",
        result={k: result[k] for k in ("url_count", "fetched", "failed")},
    )
    return result
