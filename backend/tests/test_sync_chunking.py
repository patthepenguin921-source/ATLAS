"""A real account's sync can take several chunks to finish (see
run_sync_step's docstring in app.integrations) — `POST /sync` runs one
deadline-bounded chunk and returns right away instead of blocking the
request for the sync's whole duration. These tests cover the orchestration
around that: a chunk that says "more to do" leaves the row claimable again
for the *next* chunk (without a fresh "Sync now" needing to fight the
already-running claim), a chunk that finishes clears that back out, and
`run_sync` (used by cron/connect, which want one blocking answer) loops
chunks internally until done.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import app.integrations as integrations_module

USER_ID = str(uuid.uuid4())


class _ChunkingProvider:
    """Simulates a resumable provider: returns `continue: True` for the
    first `continues` calls, then a final "done" result."""

    def __init__(self, continues: int, final: dict[str, Any] | None = None):
        self._continues = continues
        self._final = final or {"courses": 9}
        self.calls = 0

    async def sync(self, user_id: str, *, deadline: float | None = None) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self._continues:
            return {"continue": True, "courses": self.calls * 3}
        return dict(self._final)


def _install_fake_db(monkeypatch, *, initial_status: str, initial_config: dict[str, Any] | None = None):
    """A minimal in-memory stand-in for the integrations row, tracking just
    enough (`status`, `config`) for the claim/resume logic under test."""
    state = {"status": initial_status, "config": initial_config or {}}
    claim_calls: list[dict] = []

    async def _fake_update(table, patch, *, filters):
        if filters.get("updated_at", "").startswith("lt."):
            return []  # reconcile_stale_syncs sweep — nothing stale in these tests
        if filters.get("status") == "neq.running":
            claim_calls.append(dict(filters))
            if state["status"] == "running":
                return []  # already running — claim fails
            state["status"] = "running"
            return [{"id": "row-1", "status": "running"}]
        # _set_status / provider progress-save style update
        if "status" in patch:
            state["status"] = patch["status"]
        if "config" in patch:
            state["config"] = patch["config"]
        return [{"id": "row-1", **patch}]

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        if columns == "status,config":
            return [{"status": state["status"], "config": state["config"]}]
        if columns == "status":
            return [{"status": state["status"]}]
        return [{"status": state["status"], "config": state["config"]}]

    monkeypatch.setattr(integrations_module.supabase, "update", _fake_update)
    monkeypatch.setattr(integrations_module.supabase, "select", _fake_select)
    return state, claim_calls


def test_run_sync_step_returns_after_exactly_one_chunk(monkeypatch):
    state, claim_calls = _install_fake_db(monkeypatch, initial_status="idle")
    provider = _ChunkingProvider(continues=3)
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": provider})

    result = asyncio.run(integrations_module.run_sync_step("schoology", USER_ID))

    assert result["status"] == "running"
    assert result["continue"] is True
    assert provider.calls == 1  # one chunk only — did not loop to completion
    assert state["status"] == "running"
    assert len(claim_calls) == 1  # claimed once, for this first chunk


def test_run_sync_step_resumes_a_paused_chunk_without_reclaiming(monkeypatch):
    # Row is already "running" with a resume marker left by a prior chunk —
    # simulating the state right after run_sync_step's first call above.
    state, claim_calls = _install_fake_db(
        monkeypatch, initial_status="running",
        initial_config={"_sync_progress": {"pending": [["c1", "Bio", "s1", None]], "report": {"courses": 3}}},
    )
    provider = _ChunkingProvider(continues=0, final={"courses": 9, "documents": 4})
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": provider})

    result = asyncio.run(integrations_module.run_sync_step("schoology", USER_ID))

    assert result["status"] == "success"
    assert result["courses"] == 9
    assert provider.calls == 1
    # Must not have gone through the atomic claim a second time — the whole
    # point is that a paused chunk doesn't fight its own continuation.
    assert claim_calls == []


def test_run_sync_step_rejects_a_genuinely_concurrent_second_sync(monkeypatch):
    # Row is "running" but with *no* resume marker — a real second sync
    # actually in flight, not our own paused chunk. Must still be rejected.
    state, claim_calls = _install_fake_db(monkeypatch, initial_status="running", initial_config={})
    provider = _ChunkingProvider(continues=0)
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": provider})

    result = asyncio.run(integrations_module.run_sync_step("schoology", USER_ID))

    assert result["status"] == "error"
    assert "already running" in result["detail"]
    assert provider.calls == 0


def test_run_sync_loops_chunks_internally_until_done(monkeypatch):
    state, claim_calls = _install_fake_db(monkeypatch, initial_status="idle")
    provider = _ChunkingProvider(continues=2, final={"courses": 9, "documents": 4})
    monkeypatch.setattr(integrations_module, "PROVIDERS", {"schoology": provider})

    result = asyncio.run(integrations_module.run_sync("schoology", USER_ID))

    assert result["status"] == "success"
    assert result["courses"] == 9
    assert provider.calls == 3  # two "continue" chunks, then the final one
    assert len(claim_calls) == 1  # claimed exactly once for the whole run, not per chunk
    assert state["status"] == "success"
