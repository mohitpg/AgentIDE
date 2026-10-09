"""
Request scope + agent auth: the supervisor's user/project reach `agent.run` via
A2A metadata, and with A2A_AUTH_TOKEN set only the gateway can call the agent.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message, MessageSendParams, Part, Role, TextPart
from agent_common.base import BaseAgent
from agent_common.client import A2AAgentClient
from agent_common.executor import LLMAgentExecutor
from agent_common.scope import RequestScope, current_scope, parse_scope
from agent_common.server import BearerAuthMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

USER, PROJECT = uuid.uuid4(), uuid.uuid4()


def test_parse_scope_needs_both_valid_ids():
    assert parse_scope({"user_id": str(USER), "project_id": str(PROJECT)}) == RequestScope(
        USER, PROJECT
    )
    assert parse_scope({"user_id": str(USER)}) is None
    assert parse_scope({"user_id": "nope", "project_id": str(PROJECT)}) is None
    assert parse_scope(None) is None


def test_client_sends_scope_as_metadata():
    msg = A2AAgentClient()._build_message(
        "hi", "answer", {"user_id": str(USER), "project_id": str(PROJECT)}
    )
    assert msg.metadata == {"skill_id": "answer", "user_id": str(USER), "project_id": str(PROJECT)}


# ── executor ──────────────────────────────────────────────────────────────────
_SKILL_MD = """---
id: probe
name: Probe
description: Records its scope.
---
prompt
"""


class ScopeRecordingAgent(BaseAgent):
    id = "probe"
    name = "Probe"
    description = "records the scope it runs under"

    def __init__(self, skills_dir: Path) -> None:
        self._dir = skills_dir
        super().__init__(url="http://probe")
        self.seen: list[RequestScope | None] = []

    @property
    def skills_dir(self) -> Path:
        return self._dir

    async def run(self, skill_id, user_input, *, run_id=""):  # type: ignore[no-untyped-def]
        self.seen.append(current_scope())
        yield "ok"


@pytest.fixture
def agent(tmp_path) -> ScopeRecordingAgent:
    (tmp_path / "probe.md").write_text(_SKILL_MD, encoding="utf-8")
    return ScopeRecordingAgent(tmp_path)


def _context(metadata: dict) -> RequestContext:
    message = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text="hello"))],
        message_id=str(uuid.uuid4()),
        metadata=metadata,
    )
    return RequestContext(
        request=MessageSendParams(message=message),
        task_id=str(uuid.uuid4()),
        context_id=str(uuid.uuid4()),
    )


async def test_executor_exposes_scope_only_during_run(agent):
    meta = {"skill_id": "probe", "user_id": str(USER), "project_id": str(PROJECT)}

    await LLMAgentExecutor(agent).execute(_context(meta), EventQueue())

    assert agent.seen == [RequestScope(USER, PROJECT)]
    assert current_scope() is None  # reset afterwards


async def test_executor_without_scope_runs_unscoped(agent):
    await LLMAgentExecutor(agent).execute(_context({"skill_id": "probe"}), EventQueue())
    assert agent.seen == [None]


# ── bearer middleware ─────────────────────────────────────────────────────────
def _app() -> BearerAuthMiddleware:
    async def ok(_request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    inner = Starlette(routes=[
        Route("/", ok, methods=["POST"]),
        Route("/.well-known/agent-card.json", ok),
    ])
    return BearerAuthMiddleware(inner, token="s3cret")


async def test_bearer_middleware_guards_everything_but_the_card():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://agent"
    ) as c:
        assert (await c.get("/.well-known/agent-card.json")).status_code == 200
        assert (await c.post("/")).status_code == 401
        assert (await c.post("/", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.post("/", headers={"Authorization": "Bearer s3cret"})).status_code == 200
