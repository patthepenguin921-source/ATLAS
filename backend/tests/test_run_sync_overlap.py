"""run_sync() must not let two syncs for the same user+provider run at
once — concurrent requests racing the same authenticated Schoology session
turned out to be unreliable in production (see _SECTION_SYNC_CONCURRENCY's
history). The "running" transition is claimed with an atomic
UPDATE ... WHERE status != 'running', so a second "Sync now" click while
the first is still genuinely in flight is rejected immediately instead of
silently interfering with it.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import app.integrations as integrations_module

USER_ID = str(uuid.uuid4())


class _FakeProvider:
    def __init__(self, result: dict[str, Any]):
        self._result = result
        self.sync_calls = 0

    async def sync(self, user_id: str) -> dict[str, Any]:
        self.sync_calls += 1
        return self._result


def test_run_sync_rejects_a_second_call_while_one_is_running(monkeypatch):
    async def _fake_update(table, patch, *, filters):
        assert filters["status"] == "neq.running"
        return []  # nothing matched — a row is already status='running'

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [{"status": "running"}]

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)
    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    fake_provider = _FakeProvider({"courses": 1})
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": fake_provider})

    result = asyncio.run(integrations_module.run_sync("schoology", USER_ID))

    assert result["status"] == "error"
    assert "already running" in result["detail"]
    assert fake_provider.sync_calls == 0  # never actually started a second sync


def test_run_sync_claims_and_proceeds_when_nothing_is_running(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []

    async def _fake_update(table, patch, *, filters):
        calls.append((table, patch, filters))
        if filters.get("status") == "neq.running":
            return [{"id": "row-1", "status": "running"}]  # claimed successfully
        return [{"id": "row-1", **patch}]

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)
    fake_provider = _FakeProvider({"courses": 3, "documents": 2})
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": fake_provider})

    result = asyncio.run(integrations_module.run_sync("schoology", USER_ID))

    assert result["status"] == "success"
    assert fake_provider.sync_calls == 1
    # The claim itself was the atomic UPDATE ... WHERE status != 'running'.
    claim_calls = [c for c in calls if c[2].get("status") == "neq.running"]
    assert len(claim_calls) == 1
    assert claim_calls[0][1] == {"status": "running"}


def test_run_sync_falls_through_when_no_integration_row_exists_yet(monkeypatch):
    async def _fake_update(table, patch, *, filters):
        return []  # nothing matched — either already running, or no row at all

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return []  # no integration row exists yet

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)
    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    fake_provider = _FakeProvider({"courses": 0})
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": fake_provider})

    result = asyncio.run(integrations_module.run_sync("schoology", USER_ID))

    # No row to reject against — proceeds with the sync rather than
    # silently no-op-ing forever.
    assert result["status"] == "success"
    assert fake_provider.sync_calls == 1
