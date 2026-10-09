"""
Auth: access tokens, refresh rotation, Google ID-token verification, the /auth
endpoints, and account provisioning.

Google is faked end to end: a locally generated RSA key signs the ID token and is
served as the JWKS through an `httpx.MockTransport`, so the real verification
code runs (signature, iss, aud, exp, at_hash, email_verified) without network.
The DB is in-memory SQLite with `get_db` overridden.
"""

from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from api.config import Settings
from api.db.session import get_db
from api.main import app
from api.models import Conversation, Image, Project, RefreshToken, Task, User
from api.routers.auth import get_google_oauth
from api.services.google_oauth import (
    JWKS_URL,
    TOKEN_URL,
    GoogleAuthError,
    GoogleIdentity,
    GoogleOAuth,
)
from api.services.tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)
from api.services.users import upsert_google_user
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwk, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

CLIENT_ID = "test-client.apps.googleusercontent.com"
SETTINGS = Settings(
    secret_key="test-secret",
    google_client_id=CLIENT_ID,
    google_client_secret="shh",
    auth_admin_emails="",
)


# ── fake Google ───────────────────────────────────────────────────────────────
class FakeGoogle:
    """Signs ID tokens with a local RSA key and serves the token + JWKS endpoints."""

    def __init__(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        jwk_dict = jwk.construct(public, "RS256").to_dict()
        self.jwks = {
            "keys": [
                {k: v.decode() if isinstance(v, bytes) else v for k, v in jwk_dict.items()}
                | {"kid": "k1"}
            ]
        }
        self.claims: dict = {}
        self.token_requests: list[dict] = []

    def id_token(self, access_token: str, **overrides) -> str:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-sub-1",
            "email": "Alice@Example.com",
            "email_verified": True,
            "name": "Alice",
            "picture": "https://example.com/a.png",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        claims.update(self.claims, **overrides)
        return jwt.encode(
            claims, self._private, algorithm="RS256",
            headers={"kid": "k1"}, access_token=access_token,
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json=self.jwks)
        if str(request.url) == TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form.get("code") != "good-code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"access_token": "at-1", "id_token": self.id_token("at-1")}
            )
        return httpx.Response(404)

    def oauth(self) -> GoogleOAuth:
        return GoogleOAuth(SETTINGS, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


# ── DB ────────────────────────────────────────────────────────────────────────
@pytest.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for model in (User, Project, RefreshToken, Conversation, Task, Image):
            await conn.run_sync(model.__table__.create)
        # rag_documents is raw-SQL owned (packages/rag); just the owner columns.
        await conn.execute(text(
            "CREATE TABLE rag_documents (id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT)"
        ))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory, google, monkeypatch):
    monkeypatch.setattr("api.routers.auth.get_settings", lambda: SETTINGS)
    monkeypatch.setattr("api.services.tokens.get_settings", lambda: SETTINGS)
    monkeypatch.setattr("api.deps.get_settings", lambda: SETTINGS)

    async def override_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_google_oauth] = google.oauth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_google_oauth, None)


_LOGIN = {
    "code": "good-code",
    "code_verifier": "v" * 43,
    "redirect_uri": "http://127.0.0.1:53682/callback",
}


# ── access tokens ─────────────────────────────────────────────────────────────
def test_access_token_round_trip():
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid, SETTINGS), SETTINGS) == uid


def test_access_token_rejects_wrong_key_and_expiry():
    uid = uuid.uuid4()
    with pytest.raises(InvalidTokenError):
        decode_access_token(create_access_token(uid, SETTINGS), Settings(secret_key="other"))
    expired = Settings(secret_key="test-secret", access_token_expire_minutes=-1)
    with pytest.raises(InvalidTokenError):
        decode_access_token(create_access_token(uid, expired), SETTINGS)


def test_access_token_rejects_other_token_types():
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "refresh", "exp": int(time.time()) + 60},
        "test-secret", algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, SETTINGS)


# ── Google ID-token verification ──────────────────────────────────────────────
async def test_exchange_verifies_and_returns_identity(google):
    identity = await google.oauth().exchange_code(
        code="good-code", code_verifier="v" * 43, redirect_uri=_LOGIN["redirect_uri"]
    )
    assert identity == GoogleIdentity(
        sub="google-sub-1", email="alice@example.com", name="Alice",
        picture="https://example.com/a.png",
    )
    # The secret + PKCE verifier went to Google, not anywhere else.
    [form] = google.token_requests
    assert form["client_secret"] == "shh"
    assert form["code_verifier"] == "v" * 43


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-elses-client"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 10},
        {"email_verified": False},
    ],
)
async def test_bad_id_tokens_are_rejected(google, claims):
    google.claims = claims
    with pytest.raises(GoogleAuthError):
        await google.oauth().exchange_code(
            code="good-code", code_verifier="v" * 43, redirect_uri=_LOGIN["redirect_uri"]
        )


async def test_id_token_from_an_unknown_key_is_rejected(google):
    token = google.id_token("at-1")
    google.jwks = {"keys": []}  # Google never published this signing key
    with pytest.raises(GoogleAuthError):
        await google.oauth().verify_id_token(token, "at-1")


async def test_failed_code_exchange_is_an_error(google):
    with pytest.raises(GoogleAuthError, match="invalid_grant"):
        await google.oauth().exchange_code(
            code="bad", code_verifier="v" * 43, redirect_uri=_LOGIN["redirect_uri"]
        )


# ── /auth endpoints ───────────────────────────────────────────────────────────
async def test_login_creates_admin_user_with_default_project(client, factory):
    resp = await client.post("/auth/google", json=_LOGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["is_superuser"] is True  # first account, no admin list
    assert body["refresh_token"] and body["access_token"]

    async with factory() as db:
        user = (await db.execute(select(User))).scalar_one()
        [project] = (await db.execute(select(Project))).scalars().all()
        assert project.owner_id == user.id
        # Only the hash is stored.
        stored = (await db.execute(select(RefreshToken))).scalar_one()
        assert stored.token_hash != body["refresh_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


async def test_login_rejects_bad_code(client):
    resp = await client.post("/auth/google", json={**_LOGIN, "code": "bad"})
    assert resp.status_code == 401


async def test_login_requires_a_loopback_redirect(client):
    resp = await client.post("/auth/google", json={**_LOGIN, "redirect_uri": "https://evil.example/cb"})
    assert resp.status_code == 422


async def test_refresh_rotates_and_reuse_revokes_everything(client):
    first = (await client.post("/auth/google", json=_LOGIN)).json()

    rotated = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Replaying the old token is theft: rejected, and the live one dies with it.
    replay = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    after = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401


async def test_logout_revokes_the_refresh_token(client):
    tokens = (await client.post("/auth/google", json=_LOGIN)).json()
    body = {"refresh_token": tokens["refresh_token"]}
    assert (await client.post("/auth/logout", json=body)).status_code == 204
    resp = await client.post("/auth/refresh", json=body)
    assert resp.status_code == 401


async def test_protected_routes_need_a_valid_token(client):
    assert (await client.get("/auth/me")).status_code == 401
    bad = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401


async def test_config_exposes_only_public_values(client):
    body = (await client.get("/auth/config")).json()
    assert body["google_enabled"] is True
    assert body["google_client_id"] == CLIENT_ID
    assert "shh" not in str(body)


# ── provisioning ──────────────────────────────────────────────────────────────
def _identity(sub: str, email: str) -> GoogleIdentity:
    return GoogleIdentity(sub=sub, email=email, name=None, picture=None)


async def test_first_user_adopts_pre_auth_rows_later_users_do_not(factory):
    legacy_conv = uuid.uuid4()
    async with factory() as db:
        db.add(Conversation(id=legacy_conv, title="old chat"))
        await db.execute(text("INSERT INTO rag_documents (id) VALUES ('doc-1')"))
        await db.commit()

        first = await upsert_google_user(db, _identity("s1", "a@x.com"), SETTINGS)
        second = await upsert_google_user(db, _identity("s2", "b@x.com"), SETTINGS)
        await db.commit()

        conv = await db.get(Conversation, legacy_conv)
        assert conv.user_id == first.id and conv.project_id is not None
        owner = (await db.execute(text("SELECT user_id FROM rag_documents"))).scalar_one()
        assert uuid.UUID(str(owner)) == first.id
        assert first.is_superuser and not second.is_superuser


async def test_admin_list_is_authoritative(factory):
    settings = Settings(secret_key="x", auth_admin_emails="boss@x.com")
    async with factory() as db:
        first = await upsert_google_user(db, _identity("s1", "a@x.com"), settings)
        boss = await upsert_google_user(db, _identity("s2", "boss@x.com"), settings)
        assert not first.is_superuser  # first account, but not on the list
        assert boss.is_superuser


async def test_same_google_account_maps_to_one_user(factory):
    async with factory() as db:
        a = await upsert_google_user(db, _identity("s1", "a@x.com"), SETTINGS)
        # Email changed at Google; the stable `sub` still identifies the account.
        b = await upsert_google_user(db, _identity("s1", "new@x.com"), SETTINGS)
        assert a.id == b.id and b.email == "new@x.com"
        assert len((await db.execute(select(Project))).scalars().all()) == 1
