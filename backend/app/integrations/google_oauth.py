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

import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.core.crypto import decrypt_json
from app.core.supabase_client import eq, supabase

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


async def resolve_access_token(integration: dict[str, Any]) -> str | None:
    """A currently-valid Drive access token for the given `integrations`
    row, refreshing from its stored refresh token (see
    `merge_google_refresh_token`) whenever the cached access token is
    missing or close to expiring, rather than ever asking the student to
    re-authorize. Returns None when Google Drive was never connected for
    this row (or the refresh itself fails). Row-agnostic on purpose --
    shared by `SchoologyProvider._resolve_google_token` (its own
    `schoology` row, where the token has lived since before a standalone
    connection existed) and `get_drive_access_token` below (the standalone
    `google_drive` row)."""
    config = integration.get("config") or {}
    access_token = config.get("google_access_token")
    expires_at = config.get("google_token_expires_at")
    if access_token and expires_at:
        try:
            if time.time() < float(expires_at):
                return access_token
        except (TypeError, ValueError):
            pass
    secret_ref = integration.get("secret_ref") or ""
    try:
        refresh_token = decrypt_json(secret_ref).get("google_refresh_token") if secret_ref else None
    except Exception:  # noqa: BLE001
        refresh_token = None
    if not refresh_token:
        return None
    try:
        tokens = await refresh_access_token(refresh_token)
    except GoogleOAuthError:
        return None
    new_access_token = tokens.get("access_token")
    if not new_access_token:
        return None
    new_expires_at = time.time() + float(tokens.get("expires_in") or 3600) - 60
    try:
        await supabase.update(
            "integrations",
            {"config": {**config, "google_access_token": new_access_token,
                         "google_token_expires_at": new_expires_at}},
            filters={"id": eq(integration["id"])},
        )
    except Exception:  # noqa: BLE001
        pass  # still usable for this call even if persisting the cache fails
    return new_access_token


async def get_drive_access_token(user_id: str) -> str | None:
    """Resolves a usable Drive access token for a student regardless of
    which flow connected it: the standalone `google_drive` integration row
    (see `POST /integrations/google/connect`, no longer gated on Schoology
    being connected first) or, for anyone who connected before that row
    existed, the token still merged onto their `schoology` row. Used by the
    chat agent's `pull_google_drive_document` tool (see `app.agents.tools`).
    Returns None if Google Drive was never connected at all."""
    rows = await supabase.select(
        "integrations", filters={"user_id": eq(user_id), "provider": "in.(google_drive,schoology)"},
    ) or []
    by_provider = {r["provider"]: r for r in rows}
    integration = by_provider.get("google_drive") or by_provider.get("schoology")
    if not integration or not (integration.get("config") or {}).get("google_connected"):
        return None
    return await resolve_access_token(integration)
