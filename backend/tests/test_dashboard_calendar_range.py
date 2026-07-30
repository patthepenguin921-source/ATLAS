"""`GET /dashboard/calendar-range` -- the data behind the traditional
month/week calendar view: every calendar event and assignment due date in a
[start, end) window across all courses, in one call.
"""
from __future__ import annotations

import asyncio
import uuid

import app.routers.dashboard as dashboard_module
from app.core.security import CurrentUser

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())
USER = CurrentUser(id=USER_ID, email="student@example.com")


def test_calendar_range_queries_both_tables_with_an_inclusive_exclusive_range(monkeypatch):
    calls = []

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        calls.append((table, dict(filters or {}), order, limit))
        if table == "calendar_events":
            return [{"id": "e1", "title": "Unit 1 Test", "starts_at": "2026-08-21T00:00:00+00:00",
                     "all_day": True, "kind": "class", "course_id": COURSE_ID}]
        if table == "assignments":
            return [{"id": "a1", "title": "Lab Report", "due_date": "2026-08-09T00:00:00+00:00",
                     "category": "lab", "course_id": COURSE_ID, "status": "not_started"}]
        return []

    monkeypatch.setattr(dashboard_module.supabase, "select", _fake_select)

    result = asyncio.run(dashboard_module.calendar_range(start="2026-08-01", end="2026-09-01", user=USER))

    assert result["events"][0]["id"] == "e1"
    assert result["assignments"][0]["id"] == "a1"

    events_call = next(f for t, f, o, lim in calls if t == "calendar_events")
    assignments_call = next(f for t, f, o, lim in calls if t == "assignments")
    assert events_call["starts_at"] == "gte.2026-08-01"
    assert events_call["and"] == "(starts_at.lt.2026-09-01)"
    assert assignments_call["due_date"] == "gte.2026-08-01"
    assert assignments_call["and"] == "(due_date.lt.2026-09-01)"


def test_calendar_range_returns_empty_lists_when_nothing_in_range(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return []

    monkeypatch.setattr(dashboard_module.supabase, "select", _fake_select)

    result = asyncio.run(dashboard_module.calendar_range(start="2026-01-01", end="2026-02-01", user=USER))

    assert result == {"events": [], "assignments": []}
