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

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings
from api.db.session import engine
from api.routers import health

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # ── Startup ──────────────────────────────────────────────────────────────
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
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

# Phase 1+: uncomment as you implement each router
# from api.routers import chat, sessions, agents, tasks, rag, projects, mcp
# app.include_router(chat.router,     prefix="/chat",     tags=["chat"])
# app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
