"""
FastAPI application entry point.

Routers added here grow with each phase:
  Phase 0 – /health
  Phase 1 – /chat  /sessions
  Phase 2 – /agents  /tasks
  Phase 4 – /rag  /projects
  Phase 6 – /mcp
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

# psycopg's async pool (used by the LangGraph Postgres checkpointer) cannot run
# on Windows' default ProactorEventLoop. Select the SelectorEventLoop before
# uvicorn creates its loop. Must run before any event loop is started.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from orchestrator import LLMConfig, Orchestrator  # noqa: E402

from api.config import get_settings  # noqa: E402
from api.db.session import engine  # noqa: E402
from api.routers import agents, chat, health, sessions  # noqa: E402

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # ── Startup ──────────────────────────────────────────────────────────────
    orchestrator = Orchestrator(
        LLMConfig(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        ),
        checkpointer_conn_string=settings.database_url_psycopg,
    )
    await orchestrator.startup()
    app.state.orchestrator = orchestrator
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    await orchestrator.shutdown()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# CORS – tighten allowed origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else ["app://agentide"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])

# Phase 2+: uncomment as you implement each router
# from api.routers import tasks, rag, projects, mcp
