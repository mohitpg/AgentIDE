"""
/auth — Google sign-in and session tokens.

  GET  /auth/config    public client id + endpoint for the desktop's OAuth flow
  POST /auth/google    {code, code_verifier, redirect_uri} → token pair + user
  POST /auth/refresh   {refresh_token} → new token pair (old one is revoked)
  POST /auth/logout    {refresh_token} → revoke it
  GET  /auth/me        the signed-in user

The desktop never sees the Google client secret: it gets the authorization code
over its loopback redirect and hands it here with the PKCE verifier; the gateway
does the exchange, verifies the ID token, and issues its own JWT access token and
rotating refresh token (see `api.services.tokens`).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db.session import get_db
from api.deps import get_current_user
from api.models.user import User
from api.schemas.auth import (
    AuthConfig,
    GoogleLoginRequest,
    RefreshRequest,
    TokenResponse,
    UserRead,
)
from api.services.google_oauth import AUTH_URL, SCOPES, GoogleAuthError, GoogleOAuth
from api.services.tokens import (
    InvalidTokenError,
    TokenPair,
    issue_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
)
from api.services.users import AccountDisabledError, upsert_google_user

logger = logging.getLogger(__name__)
router = APIRouter()


@lru_cache
def _google() -> GoogleOAuth:
    # One instance per process so the JWKS cache is shared across requests.
    return GoogleOAuth(get_settings())


def get_google_oauth() -> GoogleOAuth:
    return _google()


def _response(pair: TokenPair, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=UserRead.model_validate(user),
    )


@router.get("/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    s = get_settings()
    return AuthConfig(
        google_enabled=bool(s.google_client_id and s.google_client_secret),
        google_client_id=s.google_client_id,
        authorization_endpoint=AUTH_URL,
        scopes=SCOPES,
    )


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
    google: GoogleOAuth = Depends(get_google_oauth),
) -> TokenResponse:
    try:
        identity = await google.exchange_code(
            code=payload.code,
            code_verifier=payload.code_verifier,
            redirect_uri=payload.redirect_uri,
        )
    except GoogleAuthError as exc:
        logger.warning("Google sign-in failed: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    try:
        user = await upsert_google_user(db, identity, get_settings())
    except AccountDisabledError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled") from exc

    pair = await issue_tokens(db, user)
    await db.commit()
    return _response(pair, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        user, pair = await rotate_refresh_token(db, payload.refresh_token)
    except InvalidTokenError as exc:
        # Commit first: a replayed token revokes the user's other tokens, and
        # raising would otherwise roll that back.
        await db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    await db.commit()
    return _response(pair, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    await revoke_refresh_token(db, payload.refresh_token)
    await db.commit()


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
