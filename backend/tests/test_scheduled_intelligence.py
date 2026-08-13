"""`app.services.scheduled_intelligence` -- the cron-friendly "run this for
every user" wrappers around the daily plan, weekly review, and retention
refresh, mirroring `app.integrations.run_sync_for_all`'s pattern. Before
this module existed, none of these three ever ran on Vercel Cron or Cloud
Scheduler at all -- only an n8n workflow acting as one specific user could
trigger them."""
from __future__ import annotations

import asyncio
import uuid

import app.services.scheduled_intelligence as scheduled

USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())


class _FakeSupabase:
    def __init__(self, user_ids):
        self.user_ids = user_ids

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "profiles"
        return [{"id": uid} for uid in self.user_ids]


def _install(monkeypatch, user_ids):
    monkeypatch.setattr(scheduled, "supabase", _FakeSupabase(user_ids))


class _FakePlanner:
    calls: list[tuple] = []

    async def generate_daily_plan(self, user_id, plan_date, available_minutes):
        _FakePlanner.calls.append((user_id, plan_date, available_minutes))
        if user_id == "boom":
            raise RuntimeError("planner exploded")


class _FakeCoach:
    calls: list[str] = []

    async def weekly_review(self, user_id):
        _FakeCoach.calls.append(user_id)
        if user_id == "boom":
            raise RuntimeError("coach exploded")


def test_run_daily_plans_for_all_covers_every_user_and_isolates_failures(monkeypatch):
    _FakePlanner.calls = []
    _install(monkeypatch, [USER_A, "boom", USER_B])
    monkeypatch.setattr(scheduled, "Planner", _FakePlanner)

    async def fake_minutes(user_id, date):
        return 120

    monkeypatch.setattr(scheduled.schedule, "get_minutes_for", fake_minutes)

    result = asyncio.run(scheduled.run_daily_plans_for_all())

    assert {c[0] for c in _FakePlanner.calls} == {USER_A, "boom", USER_B}
    assert result == {"generated": 2, "failed": 1}


def test_run_weekly_reviews_for_all_covers_every_user_and_isolates_failures(monkeypatch):
    _FakeCoach.calls = []
    _install(monkeypatch, [USER_A, "boom"])
    monkeypatch.setattr(scheduled, "Coach", _FakeCoach)

    result = asyncio.run(scheduled.run_weekly_reviews_for_all())

    assert set(_FakeCoach.calls) == {USER_A, "boom"}
    assert result == {"generated": 1, "failed": 1}


def test_run_retention_refresh_for_all_sums_updated_concepts_and_isolates_failures(monkeypatch):
    _install(monkeypatch, [USER_A, "boom", USER_B])

    async def fake_refresh(user_id):
        if user_id == "boom":
            raise RuntimeError("refresh exploded")
        return 3

    monkeypatch.setattr(scheduled.knowledge_model, "refresh_retention", fake_refresh)

    result = asyncio.run(scheduled.run_retention_refresh_for_all())

    assert result == {"users_processed": 2, "failed": 1, "concepts_updated": 6}
