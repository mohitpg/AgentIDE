"""
Web Search agent — skills: search, fetch. Answers with cited sources.

Searches the web via the Serper API and fetches/extracts specific pages, then
synthesizes an answer with inline `[n]` citations and a `Sources:` footer (the
same citation contract as the RAG agent). Heavy bulk crawls run off-request on the
Celery `web` queue (`workers.tasks.web.crawl_urls`). Implemented in Phase 5.
"""
