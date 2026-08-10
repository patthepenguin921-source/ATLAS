"""Automated (unattended) sync trigger — `/integrations/cron/{provider}/sync`.

No user session exists when a scheduler (Vercel Cron, n8n, …) calls this, so
it's secured by a shared secret (`ATLAS_CRON_SECRET`) instead of a bearer JWT,
and it fans a single call out to every user who has the provider connected.
"""
from __future__ import annotations

import asyncio
import time
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

    async def _fake_run_sync(provider, user_id, *, deadline=None):
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


def test_run_sync_for_all_shares_one_deadline_across_users(monkeypatch):
    """A single request runs every connected user's sync sequentially, so
    each one handing `run_sync` a *fresh* SYNC_TIMEOUT_SECONDS budget would
    let the total add up to N x SYNC_TIMEOUT_SECONDS -- blowing well past
    the platform's own request timeout (Cloud Run defaults to 300s if never
    raised) long before a later user's turn even finishes. All users in one
    sweep must share the exact same deadline instead."""
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [{"user_id": user_a}, {"user_id": user_b}]

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return rows

    deadlines_seen: list[float | None] = []

    async def _fake_run_sync(provider, user_id, *, deadline=None):
        deadlines_seen.append(deadline)
        return {"provider": provider, "status": "success"}

    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    monkeypatch.setattr(integrations_module, "run_sync", _fake_run_sync)

    asyncio.run(integrations_module.run_sync_for_all("schoology"))

    assert len(deadlines_seen) == 2
    assert deadlines_seen[0] is not None
    assert deadlines_seen[0] == deadlines_seen[1]


def test_run_sync_for_all_stops_without_stranding_remaining_users_when_sweep_budget_runs_out(monkeypatch):
    """If the shared sweep deadline (see the test above) is already spent by
    the time a later user's turn comes up, that user must simply be left
    alone this sweep -- never claimed, so never left stuck on "running" --
    rather than `run_sync` being started anyway and getting cut off
    mid-flight by the platform's own hard kill. The next scheduled fire
    picks up whoever was skipped."""
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [{"user_id": user_a}, {"user_id": user_b}]

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return rows

    called_with: list[str] = []

    async def _fake_run_sync(provider, user_id, *, deadline=None):
        called_with.append(user_id)
        # Real (tiny) sleep, not a faked clock -- asyncio's own event loop
        # relies on time.monotonic() internally for its scheduling, so
        # patching that global out from under it is unsafe. Shrinking
        # SYNC_TIMEOUT_SECONDS instead lets this sleep alone burn through
        # the whole (real) sweep budget between the two users' checks.
        await asyncio.sleep(0.05)
        return {"provider": provider, "status": "success"}

    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    monkeypatch.setattr(integrations_module, "run_sync", _fake_run_sync)
    monkeypatch.setattr(integrations_module, "SYNC_TIMEOUT_SECONDS", 0.01)

    report = asyncio.run(integrations_module.run_sync_for_all("schoology"))

    assert called_with == [user_a]  # user_b never even started this sweep
    assert report["synced"] == 1
