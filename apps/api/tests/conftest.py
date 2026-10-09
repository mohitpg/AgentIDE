"""
Shared pytest fixtures for the API test suite.

The `client` fixture creates an ASGI test client that exercises the full
FastAPI app without spinning up a real server.  DB/Redis checks in the health
endpoint are allowed to fail (returns "degraded") when the services aren't
available in the test environment.

The `auth` fixture stands in for authentication by overriding the auth
dependencies, so router tests don't need a users/projects table or real tokens:

    user = auth.make_user(admin=False)
    auth.as_user(user, project_id)  # a signed-in user acting in a project
    auth.as_service()               # an agent using the service token
"""

from __future__ import annotations

import uuid

import pytest
from api.deps import Principal, Scope, get_principal, get_scope
from api.main import app
from api.models.user import User
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


def make_user(*, admin: bool = False, email: str | None = None) -> User:
    uid = uuid.uuid4()
    return User(
        id=uid,
        email=email or f"{uid.hex[:8]}@example.com",
        is_active=True,
        is_superuser=admin,
    )


class AuthOverrides:
    make_user = staticmethod(make_user)

    def as_user(self, user: User, project_id: uuid.UUID | None = None) -> Scope:
        scope = Scope(user_id=user.id, project_id=project_id or uuid.uuid4())
        app.dependency_overrides[get_principal] = lambda: Principal(user=user)
        app.dependency_overrides[get_scope] = lambda: scope
        return scope

    def as_service(self) -> None:
        app.dependency_overrides[get_principal] = lambda: Principal(user=None, is_service=True)
        app.dependency_overrides.pop(get_scope, None)

    def clear(self) -> None:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_scope, None)


@pytest.fixture
def auth():
    overrides = AuthOverrides()
    yield overrides
    overrides.clear()
