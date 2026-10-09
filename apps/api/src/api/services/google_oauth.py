"""
Google sign-in — the server half of the desktop OAuth flow.

The desktop app (Electron main process) runs the user-facing half: it opens the
system browser at Google's consent page with a PKCE challenge and a loopback
`redirect_uri` (http://127.0.0.1:<port>/callback), then hands the returned
`code` + `code_verifier` to `POST /auth/google`. This module finishes the flow:

  1. exchange the code at Google's token endpoint (with the client secret, which
     only the gateway holds),
  2. verify the returned ID token: RS256 signature against Google's JWKS, `iss`,
     `aud` == our client id, `exp`, and `email_verified`,
  3. return the verified identity claims.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from jose import JWTError, jwt

from api.config import Settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"

# Google rotates signing keys roughly weekly and publishes overlapping keys, so an
# hour-long cache is safe; an unknown `kid` forces an early refetch regardless.
_JWKS_TTL_SECONDS = 3600


class GoogleAuthError(Exception):
    """The code exchange failed or the ID token didn't verify."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


class GoogleOAuth:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret
        self._transport = transport  # injectable for tests
        self._jwks: dict | None = None
        self._jwks_fetched_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    async def _get_jwks(self, *, force: bool = False) -> dict:
        stale = time.monotonic() - self._jwks_fetched_at > _JWKS_TTL_SECONDS
        if self._jwks is None or stale or force:
            async with self._http() as http:
                resp = await http.get(JWKS_URL)
                resp.raise_for_status()
                self._jwks = resp.json()
                self._jwks_fetched_at = time.monotonic()
        return self._jwks

    async def _signing_key(self, id_token: str) -> dict:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except JWTError as exc:
            raise GoogleAuthError("malformed ID token") from exc
        for force in (False, True):
            keys = (await self._get_jwks(force=force)).get("keys", [])
            for key in keys:
                if key.get("kid") == kid:
                    return key
        raise GoogleAuthError("ID token signed with an unknown key")

    async def verify_id_token(
        self, id_token: str, access_token: str | None = None
    ) -> GoogleIdentity:
        key = await self._signing_key(id_token)
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=ISSUERS,
                # Google's ID token carries `at_hash`; jose checks it against this.
                access_token=access_token,
            )
        except JWTError as exc:
            raise GoogleAuthError(f"ID token rejected: {exc}") from exc

        email = claims.get("email")
        if not email or claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("Google account has no verified email")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(email).lower(),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> GoogleIdentity:
        if not self.configured:
            raise GoogleAuthError("Google sign-in is not configured on the server")
        async with self._http() as http:
            resp = await http.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if resp.status_code != 200:
            # Google's body is `{"error": "invalid_grant", "error_description": …}`.
            try:
                body = resp.json()
                detail = body.get("error_description") or body.get("error")
            except ValueError:
                detail = None
            raise GoogleAuthError(f"code exchange failed: {detail or resp.status_code}")

        tokens = resp.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise GoogleAuthError("Google returned no ID token")
        return await self.verify_id_token(id_token, tokens.get("access_token"))
