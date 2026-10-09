"""
Session tokens: short-lived JWT access tokens + rotating opaque refresh tokens.

  access   — HS256 JWT `{sub: user_id, typ: "access", iat, exp}`; stateless, sent
             as `Authorization: Bearer …` on REST and in the first frame of a
             WebSocket. Short-lived (`access_token_expire_minutes`).
  refresh  — 256-bit random string, stored only as its SHA-256. Single-use: each
             refresh revokes the presented token and issues a new pair. Replaying a
             revoked token is treated as theft and revokes all of the user's tokens.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.models.refresh_token import RefreshToken
from api.models.user import User

_ACCESS = "access"


class InvalidTokenError(Exception):
    """The token is malformed, expired, revoked, or not the expected type."""


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int  # access-token lifetime in seconds


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres timestamptz is already aware.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── access tokens ─────────────────────────────────────────────────────────────
def create_access_token(user_id: uuid.UUID, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    now = _now()
    claims = {
        "sub": str(user_id),
        "typ": _ACCESS,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(claims, s.secret_key, algorithm=s.algorithm)


def decode_access_token(token: str, settings: Settings | None = None) -> uuid.UUID:
    """Return the user id an access token was issued to, or raise InvalidTokenError."""
    s = settings or get_settings()
    try:
        claims = jwt.decode(token, s.secret_key, algorithms=[s.algorithm])
    except JWTError as exc:  # bad signature, expired, malformed
        raise InvalidTokenError(str(exc)) from exc
    if claims.get("typ") != _ACCESS:
        raise InvalidTokenError("not an access token")
    try:
        return uuid.UUID(str(claims.get("sub")))
    except ValueError as exc:
        raise InvalidTokenError("bad subject") from exc


# ── refresh tokens ────────────────────────────────────────────────────────────
async def issue_tokens(
    db: AsyncSession, user: User, settings: Settings | None = None
) -> TokenPair:
    """Mint a new access token + refresh token for ``user`` (caller commits)."""
    s = settings or get_settings()
    raw = secrets.token_urlsafe(32)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(days=s.refresh_token_expire_days),
        )
    )
    await db.flush()
    return TokenPair(
        access_token=create_access_token(user.id, s),
        refresh_token=raw,
        expires_in=s.access_token_expire_minutes * 60,
    )


async def revoke_all(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def rotate_refresh_token(
    db: AsyncSession, raw: str, settings: Settings | None = None
) -> tuple[User, TokenPair]:
    """Exchange a refresh token for a new pair, revoking the old one.

    Raises InvalidTokenError if the token is unknown, expired, or revoked, or
    the user is disabled. A revoked token means it was replayed: every token the
    user holds is revoked too (the caller must commit for that to stick).
    """
    # Row lock so two concurrent refreshes with one token can't both rotate it.
    row = (
        await db.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(raw))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise InvalidTokenError("unknown refresh token")
    if row.revoked_at is not None:
        await revoke_all(db, row.user_id)
        raise InvalidTokenError("refresh token reused")
    if _aware(row.expires_at) <= _now():
        raise InvalidTokenError("refresh token expired")

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("user disabled")

    row.revoked_at = _now()
    return user, await issue_tokens(db, user, settings)


async def revoke_refresh_token(db: AsyncSession, raw: str) -> None:
    """Revoke one refresh token (sign-out). Unknown tokens are ignored."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
