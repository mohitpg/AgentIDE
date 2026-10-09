"""
The caller's row scope for the current A2A request.

The supervisor sends the signed-in user + active project as A2A message metadata
(`user_id`, `project_id`); `LLMAgentExecutor` puts them here for the duration of
`agent.run`. Agents that touch user data read it instead of threading it through
every `run` signature:

  * RAG searches only that project's documents (and refuses to search without it),
  * Vision fetches attached images from the gateway on that user's behalf.

A ContextVar is per-task, so concurrent requests on one agent never see each
other's scope.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestScope:
    user_id: uuid.UUID
    project_id: uuid.UUID


_current: ContextVar[RequestScope | None] = ContextVar("agent_request_scope", default=None)


def parse_scope(meta: object) -> RequestScope | None:
    """Build a scope from A2A metadata; None unless both ids are valid UUIDs."""
    if not isinstance(meta, dict):
        return None
    try:
        return RequestScope(
            user_id=uuid.UUID(str(meta.get("user_id") or "")),
            project_id=uuid.UUID(str(meta.get("project_id") or "")),
        )
    except ValueError:
        return None


def current_scope() -> RequestScope | None:
    return _current.get()


def set_scope(scope: RequestScope | None):  # type: ignore[no-untyped-def]
    """Set the scope; returns a token for `reset_scope`."""
    return _current.set(scope)


def reset_scope(token) -> None:  # type: ignore[no-untyped-def]
    _current.reset(token)
