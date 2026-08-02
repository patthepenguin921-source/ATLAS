"""`GET/POST /integrations/google/{connect,callback,disconnect}` — the
persistent Google Drive connect flow for the Schoology sync (distinct from
the Documents page's one-off Drive Picker import). Exercised against the
real FastAPI app with `supabase`/`google_oauth` monkeypatched, no live
Google or Supabase credentials needed.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

import app.core.crypto as crypto_module
import app.integrations.google_oauth as google_oauth_module
from app.config import settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.supabase_client import supabase
from app.main import app
from app.core.security import CurrentUser, get_current_user

USER_ID = str(uuid.uuid4())
client = TestClient(app)


@pytest.fixture
def crypto_key(monkeypatch):
    monkeypatch.setattr(settings, "atlas_secret_key", Fernet.generate_key().decode())
    crypto_module._fernet.cache_clear()
    yield
    crypto_module._fernet.cache_clear()


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-123")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "secret-abc")
    monkeypatch.setattr(settings, "atlas_api_base_url", "https://api.example.com")
    monkeypatch.setattr(settings, "atlas_frontend_base_url", "https://app.example.com")


@pytest.fixture
def authed():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _fake_select(rows: list[dict[str, Any]]):
    async def _select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "integrations"
        out = rows
        if filters:
            for k, v in filters.items():
                if isinstance(v, str) and v.startswith("eq."):
                    want = v.split("eq.", 1)[1]
                    out = [r for r in out if str(r.get(k)) == str(want)]
                elif isinstance(v, str) and v.startswith("in.(") and v.endswith(")"):
                    wants = set(v[len("in.("):-1].split(","))
                    out = [r for r in out if str(r.get(k)) in wants]
                else:
                    out = [r for r in out if str(r.get(k)) == str(v)]
        return out[:limit] if limit else out
    return _select


def test_connect_503_when_not_configured(authed):
    r = client.get("/api/v1/integrations/google/connect")
    assert r.status_code == 503


def test_connect_returns_auth_url_without_schoology(oauth_configured, authed, monkeypatch, crypto_key):
    """Google Drive connects standalone now -- no Schoology row required
    (see the module docstring)."""
    monkeypatch.setattr(supabase, "select", _fake_select([]))
    r = client.get("/api/v1/integrations/google/connect")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=https%3A%2F%2Fapi.example.com%2Fapi%2Fv1%2Fintegrations%2Fgoogle%2Fcallback" in url


def test_connect_returns_auth_url_with_schoology(oauth_configured, authed, monkeypatch, crypto_key):
    monkeypatch.setattr(supabase, "select", _fake_select([
        {"id": "int-1", "user_id": USER_ID, "provider": "schoology", "config": {}, "secret_ref": "x"},
    ]))
    r = client.get("/api/v1/integrations/google/connect")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=https%3A%2F%2Fapi.example.com%2Fapi%2Fv1%2Fintegrations%2Fgoogle%2Fcallback" in url


def test_callback_passes_through_error_param(oauth_configured):
    r = client.get(
        "/api/v1/integrations/google/callback",
        params={"error": "access_denied"}, follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert location.startswith("https://app.example.com/settings?tab=Integrations&")
    assert "google=error" in location
    assert "detail=access_denied" in location


def test_callback_redirects_on_missing_code(oauth_configured):
    r = client.get(
        "/api/v1/integrations/google/callback", params={"state": "x"}, follow_redirects=False,
    )
    location = r.headers["location"]
    assert "google=error" in location
    assert "missing_code" in location


def test_callback_redirects_on_invalid_state(oauth_configured):
    r = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "abc", "state": "not-a-real-token"}, follow_redirects=False,
    )
    location = r.headers["location"]
    assert "google=error" in location
    assert "invalid_state" in location


def test_callback_creates_standalone_row_when_nothing_connected(oauth_configured, crypto_key, monkeypatch):
    """No Schoology (or prior Google Drive) row at all -- this is the
    student's first-ever Google connection, so a fresh standalone
    `google_drive` integration row is created instead of erroring."""
    state = encrypt_json({"user_id": USER_ID, "ts": time.time()})

    async def _fake_exchange_code(code, redirect_uri):
        return {"access_token": "atok", "refresh_token": "rtok", "expires_in": 3600}

    inserted: list[dict] = []

    async def _fake_insert(table, row, *, upsert=False, on_conflict=None):
        inserted.append(row)
        return [{**row, "id": "new-row"}]

    monkeypatch.setattr(google_oauth_module, "exchange_code", _fake_exchange_code)
    monkeypatch.setattr(supabase, "select", _fake_select([]))
    monkeypatch.setattr(supabase, "insert", _fake_insert)

    r = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "abc", "state": state}, follow_redirects=False,
    )
    assert r.headers["location"] == "https://app.example.com/settings?tab=Integrations&google=connected"
    assert len(inserted) == 1
    row = inserted[0]
    assert row["provider"] == "google_drive"
    assert row["user_id"] == USER_ID
    assert row["config"]["google_connected"] is True
    assert row["config"]["google_access_token"] == "atok"
    stored_secret = decrypt_json(row["secret_ref"])
    assert stored_secret == {"google_refresh_token": "rtok"}


def test_callback_stores_tokens_and_redirects_connected(oauth_configured, crypto_key, monkeypatch):
    state = encrypt_json({"user_id": USER_ID, "ts": time.time()})
    row = {
        "id": "int-1", "user_id": USER_ID, "provider": "schoology",
        "config": {"domain": "https://d.schoology.com"}, "secret_ref": encrypt_json({"schoology_username": "u"}),
    }

    async def _fake_exchange_code(code, redirect_uri):
        assert code == "abc"
        assert redirect_uri == "https://api.example.com/api/v1/integrations/google/callback"
        return {"access_token": "atok", "refresh_token": "rtok", "expires_in": 3600}

    updates: list[tuple[dict, dict]] = []

    async def _fake_update(table, patch, *, filters):
        updates.append((patch, filters))
        row.update(patch)
        return [row]

    monkeypatch.setattr(google_oauth_module, "exchange_code", _fake_exchange_code)
    monkeypatch.setattr(supabase, "select", _fake_select([row]))
    monkeypatch.setattr(supabase, "update", _fake_update)

    r = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "abc", "state": state}, follow_redirects=False,
    )

    assert r.headers["location"] == "https://app.example.com/settings?tab=Integrations&google=connected"
    patch, filters = updates[0]
    assert filters == {"id": "eq.int-1"}
    assert patch["config"]["google_connected"] is True
    assert patch["config"]["google_access_token"] == "atok"
    assert patch["config"]["domain"] == "https://d.schoology.com"  # existing config preserved
    stored_secret = decrypt_json(patch["secret_ref"])
    assert stored_secret == {"schoology_username": "u", "google_refresh_token": "rtok"}


def test_disconnect_clears_google_fields(authed, crypto_key, monkeypatch):
    row = {
        "id": "int-1", "user_id": USER_ID, "provider": "schoology",
        "config": {"domain": "https://d.schoology.com", "google_connected": True,
                    "google_access_token": "atok", "google_token_expires_at": 123.0},
        "secret_ref": encrypt_json({"schoology_username": "u", "google_refresh_token": "rtok"}),
    }
    updates: list[tuple[dict, dict]] = []

    async def _fake_update(table, patch, *, filters):
        updates.append((patch, filters))
        return [row]

    monkeypatch.setattr(supabase, "select", _fake_select([row]))
    monkeypatch.setattr(supabase, "update", _fake_update)

    r = client.post("/api/v1/integrations/google/disconnect")
    assert r.status_code == 204
    patch, filters = updates[0]
    assert filters == {"id": "eq.int-1"}
    assert "google_connected" not in patch["config"]
    assert "google_access_token" not in patch["config"]
    assert patch["config"]["domain"] == "https://d.schoology.com"
    stored_secret = decrypt_json(patch["secret_ref"])
    assert stored_secret == {"schoology_username": "u"}
