"""
/images router: upload → reference, then fetch the bytes back.

Exercises the HTTP layer against in-memory SQLite (get_db overridden) with the
staging dir pointed at tmp_path — so no Postgres and no shared upload directory
are required.
"""

from __future__ import annotations

import pytest
from api.config import Settings
from api.db.session import get_db
from api.main import app
from api.models.image import Image
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def sqlite_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Image.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(sqlite_factory, tmp_path, monkeypatch, auth):
    auth.as_user(auth.make_user())
    # Stage uploads under tmp_path instead of the system temp dir.
    monkeypatch.setattr(
        "api.routers.images.get_settings",
        lambda: Settings(image_upload_dir=str(tmp_path)),
    )

    async def override_db():
        async with sqlite_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def test_upload_returns_reference_and_serves_bytes(client):
    data = b"\x89PNG\r\n\x1a\n hello"
    resp = await client.post("/images", files={"file": ("shot.png", data, "image/png")})
    assert resp.status_code == 201
    body = resp.json()
    # The reference embeds the row id exactly as the composer / agent expect it.
    assert body["ref"] == f"<Image at db.images.{body['id']}>"
    assert body["filename"] == "shot.png"
    assert body["content_type"] == "image/png"

    # The same bytes come back from GET /images/{id} (Vision agent + UI thumbnail).
    got = await client.get(f"/images/{body['id']}")
    assert got.status_code == 200
    assert got.content == data
    assert got.headers["content-type"].startswith("image/png")


async def test_images_are_private_to_their_uploader(client, auth):
    owner = auth.make_user()
    auth.as_user(owner)
    body = (
        await client.post("/images", files={"file": ("a.png", b"\x89PNG data", "image/png")})
    ).json()

    # Another user gets a 404 (not a 403, so the id's existence isn't confirmed).
    auth.as_user(auth.make_user())
    assert (await client.get(f"/images/{body['id']}")).status_code == 404

    # The owner can view it from any of their projects (history thumbnails).
    auth.as_user(owner)
    assert (await client.get(f"/images/{body['id']}")).status_code == 200


async def test_get_unknown_image_404(client):
    resp = await client.get("/images/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_empty_upload_rejected(client):
    resp = await client.post("/images", files={"file": ("empty.png", b"", "image/png")})
    assert resp.status_code == 400
