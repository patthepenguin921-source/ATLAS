"""Google OAuth (Authorization Code + offline access) so the Schoology sync
can download a Google Doc/Slides/Sheet it finds in course materials without
asking the student to hand over a token on some recurring basis.

A one-time browser consent (`build_auth_url` -> Google's consent screen ->
`exchange_code`) grants a long-lived refresh token, stored encrypted on the
student's Schoology integration row (see `schoology.merge_google_refresh_
token`/`_resolve_google_token`). A Google access token only lives about an
hour, so every sync mints a fresh one from the refresh token via
`refresh_access_token` instead of reusing a stale one or requiring
re-consent. This is distinct from `google_files.py`'s Drive Picker flow used
by "Import from Drive" on the Documents page: that one grants a short-lived,
never-persisted token for a single manual pick, and can't satisfy what the
Schoology sync needs (a token good for background use, indefinitely).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings

_AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Same scope as the Drive Picker (google_files.py's consumer) -- read access
# to any file the student's Google account can see, including a teacher's
# doc merely shared with them, not just files they own.
SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class GoogleOAuthError(RuntimeError):
    pass


def build_auth_url(state: str, redirect_uri: str) -> str:
    """`access_type=offline` is what actually gets a refresh token back;
    `prompt=consent` forces Google to reissue one even on a reconnect after
    a disconnect, since it otherwise only hands one out on a account's very
    first-ever consent for this client."""
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTH_BASE}?{urlencode(params)}"


async def exchange_code(
    code: str, redirect_uri: str, *, transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Trade a one-time authorization code for an access + refresh token.
    `transport` is test-only (mirrors `SchoologyClient`'s constructor) — lets
    a test exercise the real request shape against `httpx.MockTransport`
    instead of mocking this function away entirely."""
    async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
        r = await client.post(_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
    if r.status_code >= 300:
        raise GoogleOAuthError(f"Google token exchange failed ({r.status_code}): {r.text[:300]}")
    return r.json()


async def refresh_access_token(
    refresh_token: str, *, transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Mint a fresh short-lived access token from the long-lived refresh
    token -- called once per sync (not persisted refresh_token-side; Google
    doesn't rotate it on a plain refresh). `transport` is test-only, as above."""
    async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
        r = await client.post(_TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "grant_type": "refresh_token",
        })
    if r.status_code >= 300:
        raise GoogleOAuthError(f"Google token refresh failed ({r.status_code}): {r.text[:300]}")
    return r.json()
