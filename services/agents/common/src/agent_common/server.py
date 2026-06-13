"""
A2A server factory (real a2a-sdk).

`create_agent_app(agent)` wires a `BaseAgent` into a Starlette ASGI app that
speaks the A2A protocol:
  - GET  /.well-known/agent-card.json  → the Agent Card
  - POST /                             → JSON-RPC: message/send, message/stream,
                                          tasks/get, tasks/cancel, …

Skill execution is handled by `LLMAgentExecutor`; tasks are tracked in an
in-memory store (swap for a Postgres-backed store in Phase 7).

When `registry_enabled` is set, the app also self-registers with the gateway's
agent registry on startup and deregisters on shutdown (Phase 2), via a Starlette
lifespan attached to the built app.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from agent_common.base import BaseAgent
from agent_common.config import AgentSettings, get_agent_settings
from agent_common.executor import LLMAgentExecutor
from agent_common.registration import RegistrationManager


def _registration_lifespan(agent: BaseAgent, settings: AgentSettings):
    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        manager = RegistrationManager(
            agent_id=agent.id,
            card=agent.card.model_dump(mode="json", by_alias=True, exclude_none=True),
            gateway_url=settings.gateway_url,
            heartbeat_seconds=settings.registry_heartbeat_seconds,
            auth_token=settings.a2a_auth_token,
        )
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    return lifespan


def create_agent_app(
    agent: BaseAgent, *, settings: AgentSettings | None = None
) -> Starlette:
    settings = settings or get_agent_settings()
    handler = DefaultRequestHandler(
        agent_executor=LLMAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
    )
    app_builder = A2AStarletteApplication(agent_card=agent.card, http_handler=handler)

    lifespan = (
        _registration_lifespan(agent, settings) if settings.registry_enabled else None
    )
    return app_builder.build(lifespan=lifespan)
