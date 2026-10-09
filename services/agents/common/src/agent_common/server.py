"""
A2A server factory (real a2a-sdk).

`create_agent_app(agent)` wires a `BaseAgent` into a Starlette ASGI app that
speaks the A2A protocol:
  - GET  /.well-known/agent-card.json  → the Agent Card
  - POST /                             → JSON-RPC: message/send, message/stream,
                                          tasks/get, tasks/cancel, …

With `A2A_AUTH_TOKEN` set, every route except the public Agent Card requires
`Authorization: Bearer <token>`. That's what makes the `user_id` / `project_id`
the supervisor sends trustworthy: only the gateway can choose whose data an agent
reads.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
from collections.abc import AsyncIterator

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_common.base import BaseAgent
from agent_common.config import AgentSettings, get_agent_settings
from agent_common.executor import LLMAgentExecutor
from agent_common.registration import RegistrationManager

logger = logging.getLogger(__name__)


class BearerAuthMiddleware:
    """Pure-ASGI bearer check (safe for the streaming JSON-RPC responses)."""

    def __init__(self, app, token: str) -> None:  # type: ignore[no-untyped-def]
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        # The Agent Card is public by design (discovery); lifespan isn't HTTP.
        if scope["type"] != "http" or scope["path"].startswith("/.well-known/"):
            return await self._app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"")
        scheme, _, token = auth.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(token, self._token):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)
        return await self._app(scope, receive, send)


def _agent_lifespan(agent: BaseAgent, settings: AgentSettings):
    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Agent setup first (e.g. connect MCP servers) so the agent can serve
        # tool-backed requests as soon as it's registered.
        await agent.astartup()

        manager: RegistrationManager | None = None
        if settings.registry_enabled:
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
            if manager is not None:
                await manager.stop()
            await agent.ashutdown()

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
    app = app_builder.build(lifespan=_agent_lifespan(agent, settings))

    async def set_workspace(request: Request) -> JSONResponse:
        """POST /admin/workspace {"path": ...} — re-root the agent at runtime.

        The gateway calls this on every live agent when the user switches folders;
        agents that aren't directory-scoped inherit the no-op default and just ack.
        """
        data = await request.json()
        path = (data or {}).get("path", "")
        if not path:
            return JSONResponse({"error": "path required"}, status_code=400)
        await agent.set_workspace(path)
        return JSONResponse({"ok": True, "workspace": path})

    # Prepend so our exact-path route is matched before the A2A JSON-RPC handler.
    app.router.routes.insert(0, Route("/admin/workspace", set_workspace, methods=["POST"]))

    if settings.a2a_auth_token:
        app.add_middleware(BearerAuthMiddleware, token=settings.a2a_auth_token)
    else:
        logger.warning(
            "A2A_AUTH_TOKEN is unset: agent %s accepts unauthenticated requests "
            "(and the gateway will refuse its registration)", agent.id,
        )
    return app
