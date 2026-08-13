"""`app.integrations.base.IntegrationProvider.upsert_grade`'s `changed` flag
-- what lets a caller (see `record_from_synced_grade` in
`app.services.mistake_analysis`) react only to a grade that's actually new
or moved, not re-fire on every sync of an unchanged one."""
from __future__ import annotations

import asyncio
import uuid

import app.integrations.base as base_module
from app.integrations.base import IntegrationProvider

USER_ID = str(uuid.uuid4())
ASSIGNMENT_ID = str(uuid.uuid4())


class _FakeSupabase:
    def __init__(self, grades=None):
        self.grades = list(grades or [])
        self.updates: list[dict] = []
        self.inserts: list[dict] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "grades"
        return self.grades

    async def update(self, table, patch, *, filters):
        self.updates.append({"patch": patch, "filters": filters})
        return [patch]

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        row = {**rows, "id": str(uuid.uuid4())}
        self.inserts.append(row)
        return [row]


def _install(monkeypatch, grades=None):
    fake = _FakeSupabase(grades=grades)
    monkeypatch.setattr(base_module, "supabase", fake)
    return fake


def test_new_grade_is_always_changed(monkeypatch):
    _install(monkeypatch, grades=[])
    provider = IntegrationProvider()

    result = asyncio.run(provider.upsert_grade(USER_ID, ASSIGNMENT_ID, None, {"score": 85}))

    assert result["changed"] is True


def test_updated_grade_with_same_score_is_not_changed(monkeypatch):
    _install(monkeypatch, grades=[{"id": "g1", "score": 85}])
    provider = IntegrationProvider()

    result = asyncio.run(provider.upsert_grade(USER_ID, ASSIGNMENT_ID, None, {"score": 85}))

    assert result["id"] == "g1"
    assert result["changed"] is False


def test_updated_grade_with_different_score_is_changed(monkeypatch):
    _install(monkeypatch, grades=[{"id": "g1", "score": 70}])
    provider = IntegrationProvider()

    result = asyncio.run(provider.upsert_grade(USER_ID, ASSIGNMENT_ID, None, {"score": 92}))

    assert result["id"] == "g1"
    assert result["changed"] is True
