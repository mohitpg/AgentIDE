"""
Auth dependencies — who is calling, and which project they're acting in.

Two kinds of caller reach the gateway:

  user     — the desktop app, with `Authorization: Bearer <access JWT>`.
  service  — an agent process, with `Authorization: Bearer <A2A_AUTH_TOKEN>`.

Row scoping: every user-data endpoint depends on `get_scope`, which resolves the
`X-Project-Id` header to a project the user owns.

WebSockets can't carry headers from a browser, so `/chat/ws` and `/tasks/ws`
authenticate with a first frame instead: `{"type": "auth", "token", "project_id"}`.
The desktop's MCP bridge (`/mcp/bridge/ws`) belongs to the user, not a project,
so its first frame carries only the token.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import get_settings
from api.db.session import AsyncSessionLocal, get_db
from api.models.conversation import Conversation
from api.models.project import Project
from api.models.user import User
from api.services.tokens import InvalidTokenError, decode_access_token

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Custom close codes for the WebSocket auth handshake
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
_WS_AUTH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Principal:
    # None for a service call without X-On-Behalf-Of.
    user: User | None
    is_service: bool = False


@dataclass(frozen=True)
class Scope:
    user_id: uuid.UUID
    project_id: uuid.UUID


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def is_service_token(token: str) -> bool:
    """True iff the service secret is configured and ``token`` matches it."""
    expected = get_settings().a2a_auth_token
    return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await db.get(User, user_id)
    return user if user is not None and user.is_active else None


async def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    on_behalf_of: str | None = Header(default=None, alias="X-On-Behalf-Of"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if creds is None or not creds.credentials:
        raise _unauthorized()
    token = creds.credentials

    if is_service_token(token):
        if not on_behalf_of:
            return Principal(user=None, is_service=True)
        try:
            user = await _active_user(db, uuid.UUID(on_behalf_of))
        except ValueError:
            user = None
        if user is None:
            raise _unauthorized("Unknown X-On-Behalf-Of user")
        return Principal(user=user, is_service=True)

    try:
        user_id = decode_access_token(token)
    except InvalidTokenError as exc:
        raise _unauthorized("Invalid or expired token") from exc
    user = await _active_user(db, user_id)
    if user is None:
        raise _unauthorized("Account not found or disabled")
    return Principal(user=user)


async def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    if principal.user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A user is required")
    return principal.user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


async def require_service(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service only")
    return principal


async def owned_project(
    db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    return (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user_id)
        )
    ).scalar_one_or_none()


async def scoped_session_id(
    db: AsyncSession, scope: Scope, session_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Keep an optional chat-session link only if that session is in ``scope``."""
    if session_id is None:
        return None
    found = await db.scalar(
        select(Conversation.id).where(
            Conversation.id == session_id,
            Conversation.user_id == scope.user_id,
            Conversation.project_id == scope.project_id,
        )
    )
    return session_id if found is not None else None


async def get_scope(
    project_header: str | None = Header(default=None, alias="X-Project-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Scope:
    if not project_header:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Project-Id header required")
    try:
        project_id = uuid.UUID(project_header)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid X-Project-Id") from exc
    if await owned_project(db, user.id, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return Scope(user_id=user.id, project_id=project_id)


async def authenticate_websocket(
    websocket: WebSocket, *, session_factory: async_sessionmaker = AsyncSessionLocal
) -> Scope | None:
    """Run the first-frame auth handshake on an accepted socket.

    Returns the caller's scope, or closes the socket (4401 bad/expired token,
    4403 unknown project) and returns None.
    """
    received = await _receive_auth_frame(websocket)
    if received is None:
        return None
    user_id, frame = received
    try:
        project_id = uuid.UUID(str(frame.get("project_id") or ""))
    except ValueError:
        await _close(websocket, WS_UNAUTHORIZED, "invalid token")
        return None

    async with session_factory() as db:
        if await _active_user(db, user_id) is None:
            await _close(websocket, WS_UNAUTHORIZED, "account disabled")
            return None
        if await owned_project(db, user_id, project_id) is None:
            await _close(websocket, WS_FORBIDDEN, "project not found")
            return None
    return Scope(user_id=user_id, project_id=project_id)


async def authenticate_websocket_user(
    websocket: WebSocket, *, session_factory: async_sessionmaker = AsyncSessionLocal
) -> uuid.UUID | None:
    """First-frame auth for a socket that belongs to a user, not a project.

    Expects `{"type": "auth", "token"}`. Returns the user's id, or closes the
    socket with 4401 and returns None.
    """
    received = await _receive_auth_frame(websocket)
    if received is None:
        return None
    user_id, _ = received
    async with session_factory() as db:
        if await _active_user(db, user_id) is None:
            await _close(websocket, WS_UNAUTHORIZED, "account disabled")
            return None
    return user_id


async def _receive_auth_frame(websocket: WebSocket) -> tuple[uuid.UUID, dict] | None:
    """Read the first frame and verify its token; close the socket (4401) if bad."""
    try:
        frame = await asyncio.wait_for(websocket.receive_json(), _WS_AUTH_TIMEOUT_SECONDS)
    except Exception:  # timeout, non-JSON, or client gone
        await _close(websocket, WS_UNAUTHORIZED, "auth frame required")
        return None

    if not isinstance(frame, dict) or frame.get("type") != "auth":
        await _close(websocket, WS_UNAUTHORIZED, "auth frame required")
        return None
    try:
        user_id = decode_access_token(str(frame.get("token") or ""))
    except InvalidTokenError:
        await _close(websocket, WS_UNAUTHORIZED, "invalid token")
        return None
    return user_id, frame


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:  # already gone
        logger.debug("websocket close failed", exc_info=True)
