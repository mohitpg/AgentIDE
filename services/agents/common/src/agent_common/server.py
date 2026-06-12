"""
A2A server factory (real a2a-sdk).

`create_agent_app(agent)` wires a `BaseAgent` into a Starlette ASGI app that
speaks the A2A protocol:
  - GET  /.well-known/agent-card.json  → the Agent Card
  - POST /                             → JSON-RPC: message/send, message/stream,
                                          tasks/get, tasks/cancel, …

Skill execution is handled by `LLMAgentExecutor`; tasks are tracked in an
in-memory store (swap for a Postgres-backed store in Phase 7).
"""

from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from agent_common.base import BaseAgent
from agent_common.executor import LLMAgentExecutor


def create_agent_app(agent: BaseAgent) -> Starlette:
    handler = DefaultRequestHandler(
        agent_executor=LLMAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
    )
    app_builder = A2AStarletteApplication(agent_card=agent.card, http_handler=handler)
    return app_builder.build()
