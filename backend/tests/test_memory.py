"""app.services.memory — grounded context assembly for the AI agents.

Regression coverage for the bug where a student asking about an upcoming
test got told nothing was on file: a test/exam synced as a `calendar_events`
row (e.g. Schoology's section-calendar `kind="exam"` items -- see
`SchoologyProvider._sync_section`) never made it into the context handed to
Claude, because `build_context`/`render_context` only ever looked at the
`assignments` table.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.core.supabase_client import supabase
from app.services import memory

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())


class FakeSupabase:
    """Returns canned rows keyed by table name, ignoring filters -- the
    queries under test are already exercised for their SQL shape elsewhere;
    here we only care that `memory` wires the calendar_events table into the
    context and renders it."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return list(self.tables.get(table, []))

    async def rpc(self, *args, **kwargs):
        return []


@pytest.fixture
def fake_db(monkeypatch):
    tables = {
        "courses": [{"id": COURSE_ID, "name": "AP Calculus BC"}],
        "assignments": [],
        "calendar_events": [
            {
                "id": str(uuid.uuid4()), "title": "Unit 3 Test", "kind": "exam",
                "course_id": COURSE_ID, "starts_at": "2026-08-05T13:00:00+00:00",
                "all_day": False, "description": None,
            },
        ],
        "grades": [], "student_knowledge": [], "concepts": [], "mistakes": [],
        "user_facts": [], "documents": [],
    }
    fake = FakeSupabase(tables)
    monkeypatch.setattr(supabase, "select", fake.select)
    monkeypatch.setattr(supabase, "rpc", fake.rpc)
    return fake


def test_build_context_includes_upcoming_calendar_events(fake_db):
    ctx = asyncio.run(memory.build_context(USER_ID, None, include_semantic=False))
    assert len(ctx["upcoming_events"]) == 1
    assert ctx["upcoming_events"][0]["title"] == "Unit 3 Test"
    assert ctx["upcoming_events"][0]["kind"] == "exam"


def test_render_context_surfaces_an_exam_event_even_with_no_matching_assignment():
    """The exact reported failure: no `assignments` row for the test, but a
    `calendar_events` row exists -- it must still show up in what Claude
    sees."""
    ctx = {
        "courses": [{"id": COURSE_ID, "name": "AP Calculus BC"}],
        "upcoming": [],
        "upcoming_events": [
            {
                "title": "Unit 3 Test", "kind": "exam", "course_id": COURSE_ID,
                "starts_at": "2026-08-05T13:00:00+00:00", "all_day": False,
            },
        ],
    }
    text = memory.render_context(ctx)
    assert "Unit 3 Test" in text
    assert "exam" in text
    assert "AP Calculus BC" in text
