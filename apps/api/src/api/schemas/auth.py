"""Pydantic DTOs for the /auth API."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class AuthConfig(BaseModel):
    """What the desktop needs to start the Google flow (all public values)."""

    google_enabled: bool
    google_client_id: str
    authorization_endpoint: str
    scopes: str


class GoogleLoginRequest(BaseModel):
    code: str = Field(min_length=1)
    # PKCE verifier (RFC 7636: 43–128 chars) the desktop generated for this login.
    code_verifier: str = Field(min_length=43, max_length=128)
    # Must be the exact loopback URI used in the authorization request.
    redirect_uri: str = Field(pattern=r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    is_superuser: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead
