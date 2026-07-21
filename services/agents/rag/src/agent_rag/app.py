"""
RAG A2A service entry point.

Run standalone:
    uvicorn agent_rag.app:app --port 8103
or:
    python -m agent_rag
"""

from __future__ import annotations

import asyncio
import sys

# This agent queries Postgres via asyncpg, which cannot run on Windows' default
# Proactor loop — pin the selector loop before uvicorn creates its event loop
# (mirrors apps/api/main.py). Must run before any loop starts.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from agent_common.server import create_agent_app  # noqa: E402

from agent_rag.agent import RagAgent  # noqa: E402

app = create_agent_app(RagAgent())


def main() -> None:
    import uvicorn

    uvicorn.run("agent_rag.app:app", host="0.0.0.0", port=8103)


if __name__ == "__main__":
    main()
