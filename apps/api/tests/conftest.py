"""
Shared pytest fixtures for the API test suite.

The `client` fixture creates an ASGI test client that exercises the full
FastAPI app without spinning up a real server.  DB/Redis checks in the health
endpoint are allowed to fail (returns "degraded") when the services aren't
available in the test environment.
"""

from __future__ import annotations

import pytest
from api.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c
