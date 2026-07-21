"""Automated (unattended) sync trigger — `/integrations/cron/{provider}/sync`.

No user session exists when a scheduler (Vercel Cron, n8n, …) calls this, so
it's secured by a shared secret (`ATLAS_CRON_SECRET`) instead of a bearer JWT,
and it fans a single call out to every user who has the provider connected.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from starlette.testclient import TestClient

import app.integrations as integrations_module
import app.routers.integrations as integrations_router
from app.config import settings
from app.main import app

client = TestClient(app)


def test_cron_endpoint_503_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "")
    r = client.get("/api/v1/integrations/cron/schoology/sync")
    assert r.status_code == 503


def test_cron_endpoint_401_on_bad_secret(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "s3cr3t")
    r = client.get(
        "/api/v1/integrations/cron/schoology/sync",
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    r2 = client.get("/api/v1/integrations/cron/schoology/sync")  # no header at all
    assert r2.status_code == 401


def test_cron_endpoint_400_on_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "s3cr3t")
    r = client.get(
        "/api/v1/integrations/cron/not-a-real-provider/sync",
        headers={"Authorization": "Bearer s3cr3t"},
    )
    assert r.status_code == 400


def test_cron_endpoint_runs_sync_for_all_with_bearer_secret(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "s3cr3t")

    calls = []

    async def _fake_run_sync_for_all(provider):
        calls.append(provider)
        return {"provider": provider, "synced": 2, "errors": 0, "results": []}

    monkeypatch.setattr(integrations_router, "run_sync_for_all", _fake_run_sync_for_all)

    r = client.get(
        "/api/v1/integrations/cron/schoology/sync",
        headers={"Authorization": "Bearer s3cr3t"},
    )
    assert r.status_code == 200
    assert r.json() == {"provider": "schoology", "synced": 2, "errors": 0, "results": []}
    assert calls == ["schoology"]


def test_cron_endpoint_accepts_x_cron_secret_header(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "s3cr3t")

    async def _fake_run_sync_for_all(provider):
        return {"provider": provider, "synced": 0, "errors": 0, "results": []}

    monkeypatch.setattr(integrations_router, "run_sync_for_all", _fake_run_sync_for_all)

    r = client.post(
        "/api/v1/integrations/cron/schoology/sync",
        headers={"X-Cron-Secret": "s3cr3t"},
    )
    assert r.status_code == 200


def test_run_sync_for_all_iterates_enabled_integrations(monkeypatch):
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [{"user_id": user_a}, {"user_id": user_b}]

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "integrations"
        assert filters["provider"] == "eq.schoology"
        assert filters["enabled"] == "eq.true"
        return rows

    called_with: list[tuple[str, str]] = []

    async def _fake_run_sync(provider, user_id):
        called_with.append((provider, user_id))
        status = "error" if user_id == user_b else "success"
        return {"provider": provider, "status": status}

    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    monkeypatch.setattr(integrations_module, "run_sync", _fake_run_sync)

    report = asyncio.run(integrations_module.run_sync_for_all("schoology"))

    assert report["synced"] == 2
    assert report["errors"] == 1
    assert {c[1] for c in called_with} == {user_a, user_b}
    assert all(c[0] == "schoology" for c in called_with)
