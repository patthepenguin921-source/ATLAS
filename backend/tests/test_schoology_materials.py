"""Materials-scraper credential storage + provider wiring — the pieces that
sit between the low-level scraper client (test_schoology_scraper.py) and the
HTTP router: merging scraper credentials into the existing encrypted secret
blob, and SchoologyProvider's login/verify/debug-scrape wrappers around it.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

import app.core.crypto as crypto_module
from app.config import settings
from app.core.crypto import decrypt_json
from app.integrations.schoology import (
    SchoologyProvider,
    encrypt_api_key,
    merge_scraper_credentials,
)
from app.integrations.schoology_scraper import SchoologyScraperAuthError, SchoologyScraperClient
from app.main import app

USER_ID = str(uuid.uuid4())
client = TestClient(app)


@pytest.fixture
def crypto_key(monkeypatch):
    monkeypatch.setattr(settings, "atlas_secret_key", Fernet.generate_key().decode())
    crypto_module._fernet.cache_clear()
    yield
    crypto_module._fernet.cache_clear()


def test_merge_scraper_credentials_preserves_existing_api_key(crypto_key):
    api_key_blob = encrypt_api_key("ckey", "csecret")
    merged = merge_scraper_credentials(api_key_blob, "student@example.com", "hunter2")
    creds = decrypt_json(merged)
    assert creds == {
        "consumer_key": "ckey", "consumer_secret": "csecret",
        "schoology_username": "student@example.com", "schoology_password": "hunter2",
    }


def test_merge_scraper_credentials_with_no_existing_blob(crypto_key):
    merged = merge_scraper_credentials("", "student@example.com", "hunter2")
    creds = decrypt_json(merged)
    assert creds == {"schoology_username": "student@example.com", "schoology_password": "hunter2"}


def test_scraper_client_raises_when_materials_credentials_missing(crypto_key, monkeypatch):
    provider = SchoologyProvider()
    row = {"secret_ref": encrypt_api_key("ckey", "csecret"), "config": {"domain": "https://d.schoology.com"}}

    async def _fake_load(self, user_id):
        return row

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)

    with pytest.raises(RuntimeError, match="Materials access isn't connected"):
        asyncio.run(provider._scraper_client(USER_ID))


def test_scraper_client_raises_when_domain_missing(crypto_key, monkeypatch):
    provider = SchoologyProvider()
    row = {
        "secret_ref": merge_scraper_credentials(
            encrypt_api_key("ckey", "csecret"), "student@example.com", "hunter2"
        ),
        "config": {},  # no domain
    }

    async def _fake_load(self, user_id):
        return row

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)

    with pytest.raises(RuntimeError, match="web address"):
        asyncio.run(provider._scraper_client(USER_ID))


def test_verify_materials_login_success(crypto_key, monkeypatch):
    provider = SchoologyProvider()
    row = {
        "secret_ref": merge_scraper_credentials(
            encrypt_api_key("ckey", "csecret"), "student@example.com", "hunter2"
        ),
        "config": {"domain": "https://d.schoology.com"},
    }

    async def _fake_load(self, user_id):
        return row

    async def _fake_login(self):
        self._logged_in = True

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)
    monkeypatch.setattr(SchoologyScraperClient, "login", _fake_login)
    monkeypatch.setattr(SchoologyScraperClient, "aclose", lambda self: asyncio.sleep(0))

    result = asyncio.run(provider.verify_materials_login(USER_ID))
    assert result == {"status": "success"}


def test_verify_materials_login_bad_credentials_raises(crypto_key, monkeypatch):
    provider = SchoologyProvider()
    row = {
        "secret_ref": merge_scraper_credentials(
            encrypt_api_key("ckey", "csecret"), "student@example.com", "wrong"
        ),
        "config": {"domain": "https://d.schoology.com"},
    }

    async def _fake_load(self, user_id):
        return row

    async def _fake_login(self):
        raise SchoologyScraperAuthError("Schoology login failed — check your username and password.")

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)
    monkeypatch.setattr(SchoologyScraperClient, "login", _fake_login)
    monkeypatch.setattr(SchoologyScraperClient, "aclose", lambda self: asyncio.sleep(0))

    with pytest.raises(SchoologyScraperAuthError):
        asyncio.run(provider.verify_materials_login(USER_ID))


# ---------------------------------------------------------------------------
# Router: POST /integrations/schoology/connect-materials
# ---------------------------------------------------------------------------
class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.updated: dict[str, Any] | None = None

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return self.rows

    async def update(self, table, patch, *, filters):
        self.updated = patch
        if self.rows:
            self.rows[0].update(patch)
        return self.rows


def test_connect_materials_requires_existing_api_key_integration(monkeypatch):
    import app.routers.integrations as integrations_router

    fake = _FakeSupabase(rows=[])
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    # Auth dependency override so this test doesn't need a real Supabase JWT.
    from app.core.security import CurrentUser, get_current_user

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID)
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect-materials",
            json={"username": "student@example.com", "password": "hunter2", "domain": "https://d.schoology.com"},
        )
        assert r.status_code == 400
        assert "Connect your Schoology API key first" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_connect_materials_merges_and_verifies(crypto_key, monkeypatch):
    import app.routers.integrations as integrations_router

    row = {
        "id": "int-1", "secret_ref": encrypt_api_key("ckey", "csecret"),
        "config": {"auth_mode": "api_key", "domain": "https://d.schoology.com"},
    }
    fake = _FakeSupabase(rows=[row])
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    monkeypatch.setattr(integrations_router.supabase, "update", fake.update)

    async def _fake_verify(self, user_id):
        return {"status": "success"}

    monkeypatch.setattr(SchoologyProvider, "verify_materials_login", _fake_verify)

    from app.core.security import CurrentUser, get_current_user

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID)
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect-materials",
            json={"username": "student@example.com", "password": "hunter2"},
        )
        assert r.status_code == 201
        assert r.json() == {"status": "success"}
        # The API key must survive the merge — not be wiped out.
        merged_creds = decrypt_json(fake.updated["secret_ref"])
        assert merged_creds["consumer_key"] == "ckey"
        assert merged_creds["schoology_username"] == "student@example.com"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
