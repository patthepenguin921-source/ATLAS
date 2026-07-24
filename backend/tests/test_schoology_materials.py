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
# Router: POST /integrations/schoology/connect — the single connect step.
# Username + password (the login session) is required; a personal API key is
# an optional extra that additionally unlocks assignments/events sync. There
# used to be a separate `/connect-materials` step that required an API key
# to already be connected first — that's gone, this is the only step now.
# ---------------------------------------------------------------------------
class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.inserted: dict[str, Any] | None = None
        self.updated: dict[str, Any] | None = None

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return self.rows

    async def insert(self, table, row, *, upsert=False, on_conflict=None):
        self.inserted = dict(row)
        self.rows = [{**row, "id": (self.rows[0]["id"] if self.rows else "int-1")}]
        return self.rows

    async def update(self, table, patch, *, filters):
        self.updated = patch
        if self.rows:
            self.rows[0].update(patch)
        return self.rows


def _override_user():
    from app.core.security import CurrentUser, get_current_user

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID)
    return get_current_user


def test_connect_requires_domain(monkeypatch):
    import app.routers.integrations as integrations_router

    fake = _FakeSupabase()
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    monkeypatch.setattr(integrations_router.supabase, "insert", fake.insert)
    get_current_user = _override_user()
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect",
            json={"domain": "", "username": "student@example.com", "password": "hunter2"},
        )
        assert r.status_code == 400
        assert "web address" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_connect_with_login_only_stores_scraper_credentials_and_syncs(crypto_key, monkeypatch):
    """The common case: no API key at all — just the login. Must not require
    (or expect) a pre-existing integration row the way the old two-step flow
    did."""
    import app.routers.integrations as integrations_router

    fake = _FakeSupabase()
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    monkeypatch.setattr(integrations_router.supabase, "insert", fake.insert)
    monkeypatch.setattr(integrations_router.supabase, "update", fake.update)

    async def _fake_verify_login(self, user_id):
        return {"status": "success"}

    async def _fake_sync(self, user_id, *, deadline=None):
        return {"courses": 1, "documents": 2, "errors": []}

    monkeypatch.setattr(SchoologyProvider, "verify_materials_login", _fake_verify_login)
    monkeypatch.setattr(SchoologyProvider, "sync", _fake_sync)

    get_current_user = _override_user()
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect",
            json={
                "domain": "https://d.schoology.com",
                "username": "student@example.com", "password": "hunter2",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "success"
        assert body["courses"] == 1

        creds = decrypt_json(fake.inserted["secret_ref"])
        assert creds == {"schoology_username": "student@example.com", "schoology_password": "hunter2"}
        assert fake.inserted["config"]["auth_mode"] == "scraper"
        assert fake.inserted["config"]["domain"] == "https://d.schoology.com"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_connect_with_optional_api_key_verifies_and_merges_credentials(crypto_key, monkeypatch):
    import app.routers.integrations as integrations_router

    fake = _FakeSupabase()
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    monkeypatch.setattr(integrations_router.supabase, "insert", fake.insert)
    monkeypatch.setattr(integrations_router.supabase, "update", fake.update)

    async def _fake_api_verify(self, consumer_key, consumer_secret, api_base):
        return {"api_uid": "123", "section_count": 4}

    async def _fake_verify_login(self, user_id):
        return {"status": "success"}

    async def _fake_sync(self, user_id, *, deadline=None):
        return {"courses": 4, "errors": []}

    monkeypatch.setattr(SchoologyProvider, "verify", _fake_api_verify)
    monkeypatch.setattr(SchoologyProvider, "verify_materials_login", _fake_verify_login)
    monkeypatch.setattr(SchoologyProvider, "sync", _fake_sync)

    get_current_user = _override_user()
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect",
            json={
                "domain": "https://d.schoology.com",
                "username": "student@example.com", "password": "hunter2",
                "consumer_key": "ckey", "consumer_secret": "csecret",
            },
        )
        assert r.status_code == 201
        creds = decrypt_json(fake.inserted["secret_ref"])
        assert creds == {
            "consumer_key": "ckey", "consumer_secret": "csecret",
            "schoology_username": "student@example.com", "schoology_password": "hunter2",
        }
        assert fake.inserted["config"]["auth_mode"] == "api_key+scraper"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_connect_bad_login_raises_401_without_saving_a_broken_row(crypto_key, monkeypatch):
    import app.routers.integrations as integrations_router

    fake = _FakeSupabase()
    monkeypatch.setattr(integrations_router.supabase, "select", fake.select)
    monkeypatch.setattr(integrations_router.supabase, "insert", fake.insert)

    async def _fake_verify_login_fails(self, user_id):
        raise SchoologyScraperAuthError("Schoology login failed — check your username and password.")

    monkeypatch.setattr(SchoologyProvider, "verify_materials_login", _fake_verify_login_fails)

    get_current_user = _override_user()
    try:
        r = client.post(
            "/api/v1/integrations/schoology/connect",
            json={
                "domain": "https://d.schoology.com",
                "username": "student@example.com", "password": "wrong",
            },
        )
        assert r.status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)
