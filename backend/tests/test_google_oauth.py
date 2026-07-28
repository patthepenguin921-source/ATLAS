"""app.integrations.google_oauth — the Authorization Code + offline-access
dance that lets the Schoology sync download Google Docs in the background.
Exercised against `httpx.MockTransport` (real request shapes, no live
Google credentials) rather than mocking the module's own functions away.
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.integrations import google_oauth


@pytest.fixture(autouse=True)
def _oauth_client(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-123")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "secret-abc")


def test_build_auth_url_includes_required_params():
    url = google_oauth.build_auth_url("state-token", "https://api.example.com/integrations/google/callback")
    parsed = urlsplit(url)
    assert parsed.netloc == "accounts.google.com"
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["client-123"]
    assert q["redirect_uri"] == ["https://api.example.com/integrations/google/callback"]
    assert q["response_type"] == ["code"]
    assert q["scope"] == [google_oauth.SCOPE]
    # offline + consent are what actually get a refresh token back, including
    # on a reconnect after a disconnect (Google otherwise only issues one on
    # an account's very first-ever consent for this client).
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["state"] == ["state-token"]


def test_exchange_code_posts_correct_grant_and_returns_tokens():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = dict(parse_qs(request.content.decode()))
        return httpx.Response(200, json={
            "access_token": "atok", "refresh_token": "rtok", "expires_in": 3600,
        })

    async def run():
        return await google_oauth.exchange_code(
            "auth-code", "https://api.example.com/cb",
            transport=httpx.MockTransport(handler),
        )

    result = asyncio.run(run())

    assert result == {"access_token": "atok", "refresh_token": "rtok", "expires_in": 3600}
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["body"] == {
        "code": ["auth-code"], "client_id": ["client-123"], "client_secret": ["secret-abc"],
        "redirect_uri": ["https://api.example.com/cb"], "grant_type": ["authorization_code"],
    }


def test_exchange_code_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async def run():
        return await google_oauth.exchange_code(
            "bad-code", "https://api.example.com/cb", transport=httpx.MockTransport(handler),
        )

    with pytest.raises(google_oauth.GoogleOAuthError):
        asyncio.run(run())


def test_refresh_access_token_posts_refresh_grant():
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(parse_qs(request.content.decode()))
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["rtok"]
        return httpx.Response(200, json={"access_token": "new-atok", "expires_in": 3600})

    async def run():
        return await google_oauth.refresh_access_token("rtok", transport=httpx.MockTransport(handler))

    result = asyncio.run(run())
    assert result == {"access_token": "new-atok", "expires_in": 3600}


def test_refresh_access_token_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    async def run():
        return await google_oauth.refresh_access_token("revoked", transport=httpx.MockTransport(handler))

    with pytest.raises(google_oauth.GoogleOAuthError):
        asyncio.run(run())
