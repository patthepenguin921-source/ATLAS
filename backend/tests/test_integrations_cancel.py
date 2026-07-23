"""`POST /integrations/{provider}/cancel` — lets a user clear their own
integration row off "running" without waiting for `reconcile_stale_syncs`'s
10-minute sweep (or a legitimately slow sync they've given up on).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from starlette.testclient import TestClient

import app.integrations as integrations_module
import app.routers.integrations as integrations_router
from app.core.security import CurrentUser, get_current_user
from app.main import app

USER_ID = str(uuid.uuid4())
client = TestClient(app)


def test_cancel_sync_updates_only_a_running_row(monkeypatch):
    calls = []

    async def _fake_update(table, patch, *, filters):
        calls.append((table, patch, filters))
        return [{"id": "row-1", **patch}]

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)

    result = asyncio.run(integrations_module.cancel_sync("schoology", USER_ID))

    assert result == {"provider": "schoology", "status": "idle", "canceled": True}
    table, patch, filters = calls[0]
    assert table == "integrations"
    assert patch == {"status": "idle", "last_error": "Sync canceled."}
    assert filters == {
        "user_id": f"eq.{USER_ID}", "provider": "eq.schoology", "status": "eq.running",
    }


def test_cancel_sync_is_a_noop_when_nothing_is_running(monkeypatch):
    async def _fake_update(table, patch, *, filters):
        return []  # no row matched the status=running filter

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)

    result = asyncio.run(integrations_module.cancel_sync("schoology", USER_ID))

    assert result == {"provider": "schoology", "status": "idle", "canceled": False}


def test_cancel_endpoint_400_on_unknown_provider():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    try:
        r = client.post("/api/v1/integrations/not-a-real-provider/cancel")
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_cancel_endpoint_calls_cancel_sync_for_the_current_user(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def _fake_cancel_sync(provider, user_id):
        calls.append((provider, user_id))
        return {"provider": provider, "status": "idle", "canceled": True}

    monkeypatch.setattr(integrations_router, "cancel_sync", _fake_cancel_sync)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    try:
        r = client.post("/api/v1/integrations/schoology/cancel")
        assert r.status_code == 200
        assert r.json() == {"provider": "schoology", "status": "idle", "canceled": True}
        assert calls == [("schoology", USER_ID)]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
