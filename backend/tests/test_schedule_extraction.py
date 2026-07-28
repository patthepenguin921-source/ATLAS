"""app.services.schedule_extraction — shared "at a glance" detection +
day-by-day schedule extraction, used by both the Schoology materials-sync
path and the generic document upload pipeline (see test_document_processing_
cron.py for the wiring into `_process_document`).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.core.supabase_client import supabase
from app.services import schedule_extraction


@pytest.mark.parametrize("title", [
    "Week at a Glance",
    "Unit at a Glance",
    "Day at a Glance",
    "Unit 4 - At a Glance.pdf",
    "Chapter 7 – AT A GLANCE",
    "AT A GLANCE: Ch. 7",
    "at-a-glance.docx",
    "Glance - at a glance",
])
def test_is_glance_title_matches_any_at_a_glance_phrasing(title):
    """The regex must not require "week"/"unit"/"day" immediately before the
    phrase -- a real-world filename like "Unit 4 - At a Glance.pdf" (a
    number/dash between the keyword and the phrase) is exactly the pattern
    that was previously missed."""
    assert schedule_extraction.is_glance_title(title) is True


@pytest.mark.parametrize("title", [
    "Syllabus.pdf",
    "Homework 3",
    None,
    "",
    "Glancing over the reading",  # "glance" alone isn't the phrase
])
def test_is_glance_title_rejects_unrelated_titles(title):
    assert schedule_extraction.is_glance_title(title) is False


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {"calendar_events": [], "assignments": []}

    @staticmethod
    def _match(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if str(row.get(k)) != str(want):
                return False
        return True

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [r for r in self.tables.setdefault(table, []) if self._match(r, filters)]

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        rows = [rows] if isinstance(rows, dict) else rows
        out = []
        for r in rows:
            row = dict(r)
            row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)
            out.append(row)
        return out

    async def update(self, table, patch, *, filters):
        out = []
        for row in self.tables.setdefault(table, []):
            if self._match(row, filters):
                row.update(patch)
                out.append(row)
        return out


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase()
    for name in ("select", "insert", "update"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    return fake


USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())


def test_apply_schedule_from_doc_creates_class_events_and_assignments(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {
            "days": [
                {
                    "date": "2025-10-06", "topic": "Intro to photosynthesis",
                    "assignments": [
                        {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
                    ],
                },
            ],
        }

    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")  # settings.has_llm -> True
    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit 3 - At a Glance.pdf",
        text="Monday 10/6: Intro to photosynthesis. Lab report due 10/9.",
        source="manual", source_document_id="doc-1",
    ))

    events = fake_db.tables["calendar_events"]
    assert len(events) == 1
    assert events[0]["kind"] == "class"
    assert events[0]["course_id"] == COURSE_ID
    assert events[0]["external_id"] == f"manual:class:{COURSE_ID}:2025-10-06"

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["title"] == "Lab Report"
    assert assignments[0]["due_date"] == "2025-10-09"
    assert assignments[0]["external_source"] == "manual"


def test_apply_schedule_from_doc_is_a_noop_without_llm(fake_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", None)  # settings.has_llm -> False

    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="whatever", source="manual",
    ))

    assert fake_db.tables["calendar_events"] == []
    assert fake_db.tables["assignments"] == []
