""" FastAPI application entry point. """

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from orchestrator import LLMConfig, Orchestrator  # noqa: E402

from api.config import get_settings  # noqa: E402
from api.db.redis import redis_client  # noqa: E402
from api.db.session import AsyncSessionLocal, engine  # noqa: E402
from api.routers import (  # noqa: E402
    agents,
    chat,
    health,
    images,
    mcp,
    rag,
    sessions,
    tasks,
)
from api.services.registry import AgentRegistry  # noqa: E402
from api.services.task_events import TaskEventHub  # noqa: E402

settings = get_settings()


async def _agent_directory() -> dict[str, str]:
    """Live {agent_id: A2A URL} from the registry — the supervisor's discovery."""
    async with AsyncSessionLocal() as db:
        registry = AgentRegistry(
            db,
            redis_client,
            ttl_seconds=settings.registry_ttl_seconds,
            cache_ttl_seconds=settings.registry_cache_ttl_seconds,
        )
        return await registry.url_map()


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
        a2a_auth_token=settings.a2a_auth_token,
        directory_provider=_agent_directory,
    )
    await orchestrator.startup()
    app.state.orchestrator = orchestrator

    # Task-event hub: Redis pub/sub → persist task records + fan out to /tasks/ws.
    task_hub = TaskEventHub(settings.redis_url)
    await task_hub.start()
    app.state.task_hub = task_hub

    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    await task_hub.stop()
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
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(rag.router, prefix="/rag", tags=["rag"])
app.include_router(images.router, prefix="/images", tags=["images"])
app.include_router(mcp.router, prefix="/mcp", tags=["mcp"])


