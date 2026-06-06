"""Phase 0 exit criterion: GET /health returns 200 with expected schema."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_schema(client: AsyncClient) -> None:
    resp = await client.get("/health")
    data = resp.json()

    assert "status" in data
    assert "checks" in data
    assert "version" in data
    assert "env" in data
    assert data["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_health_checks_keys(client: AsyncClient) -> None:
    resp = await client.get("/health")
    checks = resp.json()["checks"]

    assert "api" in checks
    assert "db" in checks
    assert "redis" in checks
    assert checks["api"] == "ok"   # API itself is always ok
