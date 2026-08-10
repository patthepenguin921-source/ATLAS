"""app.services.schedule_extraction — shared "at a glance" detection +
day-by-day schedule extraction, used by both the Schoology materials-sync
path and the generic document upload pipeline (see test_document_processing_
cron.py for the wiring into `_process_document`).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
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


@pytest.mark.parametrize("title", [
    "Unit at a Glance",
    "Unit 4 - At a Glance.pdf",
    "Semester at a Glance",
    "Chapter 7 – AT A GLANCE",
    "Quarter 2 at a Glance",
])
def test_is_recurring_glance_title_matches_broader_than_a_week(title):
    """A unit/semester/quarter/chapter-scoped document is one a teacher
    keeps editing as the term progresses, so it must be flagged for
    every-sync re-checking, not treated as done after the first pull."""
    assert schedule_extraction.is_recurring_glance_title(title) is True


@pytest.mark.parametrize("title", [
    "Week at a Glance",
    "Day at a Glance",
    "At a Glance",  # no scope word at all -- not assumed recurring
    "Syllabus.pdf",
    None,
])
def test_is_recurring_glance_title_rejects_week_scoped_and_non_glance_titles(title):
    assert schedule_extraction.is_recurring_glance_title(title) is False


def test_is_glance_title_tolerates_a_missing_the_word_a():
    """A real Schoology folder was found named "Unit at Glance" (no "a")."""
    assert schedule_extraction.is_glance_title("Unit at Glance") is True
    assert schedule_extraction.is_glance_title("Week at Glance.pdf") is True


def test_is_glance_falls_back_to_content_when_the_title_does_not_say_so():
    """The reported real-world case: a Schoology item named for what it
    contains ("... Assignments List") whose own document opens with an "at
    a glance" heading -- a plain title check alone misses this entirely."""
    title = "AP Calculus BC Unit 1 Assignments List"
    text = "# AP Calculus BC Unit 1 at a Glance\n\n8/4 through 8/21/2026 ..."
    assert schedule_extraction.is_glance_title(title) is False
    assert schedule_extraction.is_glance(title=title, text=text) is True
    assert schedule_extraction.is_recurring_glance(title=title, text=text) is True


def test_is_glance_does_not_scan_far_into_the_document_body():
    """Only the opening is checked -- a document that merely mentions the
    phrase deep in unrelated prose must not be misclassified as a schedule
    document."""
    text = ("Some unrelated notes. " * 50) + "Please review this at a glance before the quiz."
    assert schedule_extraction.is_glance(title="Notes", text=text) is False


def test_is_glance_with_no_text_falls_back_to_title_only():
    assert schedule_extraction.is_glance(title="Week at a Glance", text=None) is True
    assert schedule_extraction.is_glance(title="Notes", text=None) is False


@pytest.mark.parametrize("value", ["2025-10-09", "2026-01-01"])
def test_valid_iso_date_passes_through_a_real_date(value):
    assert schedule_extraction.valid_iso_date(value) == value


@pytest.mark.parametrize("value", ["Monday", "next Friday", "TBD", "", "10/09/2025", None, 42, ["2025-10-09"]])
def test_valid_iso_date_rejects_anything_not_a_real_iso_date(value):
    assert schedule_extraction.valid_iso_date(value) is None


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "calendar_events": [], "assignments": [], "documents": [],
        }

    @staticmethod
    def _match(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            if "->>" in k:  # JSON path filter, e.g. metadata->>source_document_id
                col, prop = k.split("->>", 1)
                got = (row.get(col) or {}).get(prop)
            else:
                got = row.get(k)
            if isinstance(v, str) and v.startswith("in.("):
                wanted = v[len("in.("):-1].split(",")
                if str(got) not in wanted:
                    return False
                continue
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if str(got) != str(want):
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

    async def delete(self, table, *, filters):
        keep, removed = [], []
        for row in self.tables.setdefault(table, []):
            (removed if self._match(row, filters) else keep).append(row)
        self.tables[table] = keep
        return removed


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase()
    for name in ("select", "insert", "update", "delete"):
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
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit 3 - At a Glance.pdf",
        text="Monday 10/6: Intro to photosynthesis. Lab report due 10/9.",
        source="manual", source_document_id="doc-1",
    ))

    events = fake_db.tables["calendar_events"]
    assert len(events) == 1
    assert events[0]["kind"] == "class"
    assert events[0]["course_id"] == COURSE_ID
    assert events[0]["external_id"] == f"manual:class:{COURSE_ID}:doc-1:2025-10-06"

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["title"] == "Lab Report"
    assert assignments[0]["due_date"] == "2025-10-09"
    assert assignments[0]["external_source"] == "manual"


@pytest.mark.parametrize("bad_due_date", ["Monday", "next Friday", "TBD", "", 42])
def test_apply_schedule_from_doc_falls_back_to_the_listed_day_when_llm_due_date_is_not_a_real_date(
    fake_db, monkeypatch, bad_due_date,
):
    """The LLM is asked for "YYYY-MM-DD or null" but doesn't always comply --
    a real response once returned a bare weekday name ("Monday") lifted
    straight from the document's own "to be finished Monday" phrasing.
    Passed straight through, Postgres used to reject the whole assignment
    insert outright (`invalid input syntax for type timestamp with time
    zone`), losing the assignment entirely instead of falling back to the
    day it's listed under -- exactly what the docstring already says should
    happen when the document doesn't give an explicit due date."""
    async def _fake_complete_json(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {
            "days": [
                {
                    "date": "2025-10-06", "topic": "Kinematics",
                    "assignments": [
                        {"title": "8.6 Kinematic Practice", "due_date": bad_due_date, "category": "homework"},
                    ],
                },
            ],
        }

    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")  # settings.has_llm -> True
    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit 1 Assignments List",
        text="Kinematics -- 8.6 Kinematic Practice, to be finished Monday.",
        source="manual", source_document_id="doc-1",
    ))

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["title"] == "8.6 Kinematic Practice"
    assert assignments[0]["due_date"] == "2025-10-06"  # falls back to the day it's listed under


def test_apply_schedule_from_doc_skips_an_assignment_already_on_file(fake_db, monkeypatch):
    """A glance doc restating a due date for an assignment the real LMS
    sync already imported (e.g. via the Assignments API) must not create a
    second, duplicate row for it -- see `apply_schedule_from_doc`'s
    docstring. Only the class-schedule calendar event should still be
    created; the already-known assignment is skipped."""
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
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    fake_db.tables["assignments"].append({
        "id": str(uuid.uuid4()), "user_id": USER_ID, "course_id": COURSE_ID,
        "title": "Lab Report", "external_id": f"{COURSE_ID}:12345", "external_source": "schoology",
    })

    report: dict[str, Any] = {"events": 0, "assignments": 0, "skipped": 0, "errors": []}
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit 3 - At a Glance.pdf",
        text="Monday 10/6: Intro to photosynthesis. Lab report due 10/9.",
        source="schoology", source_document_id="doc-1", report=report,
    ))

    assert len(fake_db.tables["calendar_events"]) == 1
    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1  # still just the one pre-existing row, no duplicate
    assert report["skipped"] == 1
    assert report["assignments"] == 0


def test_apply_schedule_from_doc_is_a_noop_without_llm(fake_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", None)  # settings.has_llm -> False

    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="whatever", source="manual",
    ))

    assert fake_db.tables["calendar_events"] == []
    assert fake_db.tables["assignments"] == []


def test_apply_schedule_from_doc_updates_due_date_in_place_on_resync(fake_db, monkeypatch):
    """A re-parse of the same document that only changed an assignment's due
    date must update that same row in place -- not create a second,
    duplicate assignment -- since the external_id now embeds the source
    document id, not just the normalized title."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _v1(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _v1)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v1", source="schoology", source_document_id="doc-1",
    ))
    assert len(fake_db.tables["assignments"]) == 1
    row_id = fake_db.tables["assignments"][0]["id"]

    async def _v2(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-11", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _v2)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v2", source="schoology", source_document_id="doc-1",
    ))

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1  # updated in place, not duplicated
    assert assignments[0]["id"] == row_id
    assert assignments[0]["due_date"] == "2025-10-11"


def test_apply_schedule_from_doc_preserves_status_on_resync(fake_db, monkeypatch):
    """Re-mining a document after an unrelated edit elsewhere in it must not
    reset a student's progress on an assignment it already created -- only a
    brand-new row gets seeded "not_started"."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _v1(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _v1)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v1", source="schoology", source_document_id="doc-1",
    ))
    assert fake_db.tables["assignments"][0]["status"] == "not_started"
    fake_db.tables["assignments"][0]["status"] = "done"  # student finished it

    async def _v2(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro (typo fixed)", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _v2)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v2", source="schoology", source_document_id="doc-1",
    ))

    assert fake_db.tables["assignments"][0]["status"] == "done"


def test_apply_schedule_from_doc_deletes_assignment_and_event_dropped_from_a_resync(fake_db, monkeypatch):
    """When a re-parsed document no longer mentions a day/assignment it
    previously did, the rows that document created for them must be
    deleted -- not left behind forever."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _v1(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [
            {"date": "2025-10-06", "topic": "Intro", "assignments": [
                {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
            ]},
            {"date": "2025-10-08", "topic": "Quiz day", "assignments": [
                {"title": "Quiz 1", "due_date": "2025-10-08", "category": "quiz"},
            ]},
        ]}

    monkeypatch.setattr(claude, "complete_json", _v1)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v1", source="schoology", source_document_id="doc-1",
    ))
    assert len(fake_db.tables["assignments"]) == 2
    assert len(fake_db.tables["calendar_events"]) == 2

    # The teacher removed the quiz day entirely on a later edit.
    async def _v2(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [
            {"date": "2025-10-06", "topic": "Intro", "assignments": [
                {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
            ]},
        ]}

    monkeypatch.setattr(claude, "complete_json", _v2)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v2", source="schoology", source_document_id="doc-1",
    ))

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["title"] == "Lab Report"
    events = fake_db.tables["calendar_events"]
    assert len(events) == 1
    assert events[0]["title"] == "Intro"


def test_apply_schedule_from_doc_empty_extraction_does_not_delete_prior_rows(fake_db, monkeypatch):
    """A transient LLM failure/empty result on a re-sync must never be read
    as "the document now has zero days" and wipe out what a previous,
    successful run saved -- reconciliation only runs when this run's
    extraction actually found something."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _v1(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _v1)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v1", source="schoology", source_document_id="doc-1",
    ))
    assert len(fake_db.tables["assignments"]) == 1
    assert len(fake_db.tables["calendar_events"]) == 1

    async def _v2_empty(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": []}

    monkeypatch.setattr(claude, "complete_json", _v2_empty)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week at a Glance",
        text="v2", source="schoology", source_document_id="doc-1",
    ))

    assert len(fake_db.tables["assignments"]) == 1
    assert len(fake_db.tables["calendar_events"]) == 1


def test_apply_schedule_from_doc_cross_document_title_collision_does_not_suppress_real_item(fake_db, monkeypatch):
    """Two different glance documents that happen to mention the same
    generic assignment title (e.g. a recurring weekly "Quiz") must not
    collide -- only a genuinely independent (non-glance) import counts for
    the cross-source dedupe check."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-week1", "metadata": {}})
    fake_db.tables["documents"].append({"id": "doc-week2", "metadata": {}})

    async def _week1(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Week 1", "assignments": [
            {"title": "Quiz", "due_date": "2025-10-09", "category": "quiz"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _week1)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week 1 at a Glance",
        text="week1", source="schoology", source_document_id="doc-week1",
    ))

    async def _week2(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-13", "topic": "Week 2", "assignments": [
            {"title": "Quiz", "due_date": "2025-10-16", "category": "quiz"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _week2)
    report: dict[str, Any] = {"events": 0, "assignments": 0, "skipped": 0, "errors": []}
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Week 2 at a Glance",
        text="week2", source="schoology", source_document_id="doc-week2", report=report,
    ))

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 2  # both weeks' quizzes kept, not collided
    assert {a["due_date"] for a in assignments} == {"2025-10-09", "2025-10-16"}


def test_apply_schedule_from_doc_keeps_same_title_listed_on_different_days(fake_db, monkeypatch):
    """The reported real-world bug: a single document (a real AP Calculus
    "Unit at a Glance") listed "Study for Test" as a suggested assignment on
    both 8/19 and 8/20 -- keyed only by (document, title), the second day
    silently overwrote the first's row instead of both surviving as the two
    distinct reminders the document actually lists."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _fake(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [
            {"date": "2025-08-19", "topic": "Review", "assignments": [
                {"title": "Study for Test", "due_date": "2025-08-19", "category": "other"},
            ]},
            {"date": "2025-08-20", "topic": "Review", "assignments": [
                {"title": "Study for Test", "due_date": "2025-08-20", "category": "other"},
            ]},
        ]}

    monkeypatch.setattr(claude, "complete_json", _fake)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit at a Glance",
        text="whatever", source="schoology", source_document_id="doc-1",
    ))

    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 2  # both days' reminders kept, not collided
    assert {a["due_date"] for a in assignments} == {"2025-08-19", "2025-08-20"}


def test_apply_schedule_from_doc_records_date_range_onto_the_document_row(fake_db, monkeypatch):
    """`glance_still_relevant` reads `documents.metadata.glance_date_range` --
    this is where it gets written, merged into whatever metadata the row
    already carries rather than overwriting it."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {"content_hash": "abc123"}})

    async def _fake(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [
            {"date": "2025-10-06", "topic": "Intro", "assignments": []},
            {"date": "2025-10-10", "topic": "Wrap-up", "assignments": []},
        ]}

    monkeypatch.setattr(claude, "complete_json", _fake)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit at a Glance",
        text="whatever", source="schoology", source_document_id="doc-1",
    ))

    doc = fake_db.tables["documents"][0]
    assert doc["metadata"]["glance_date_range"] == {"start": "2025-10-06", "end": "2025-10-10"}
    assert doc["metadata"]["content_hash"] == "abc123"  # merged, not overwritten


def test_apply_schedule_from_doc_purges_rows_left_by_a_deleted_document(fake_db, monkeypatch):
    """The reported real-world bug: an earlier duplicate-ingest of the same
    Schoology item got cleaned up by deleting its stale `documents` row, but
    the class-schedule `calendar_events`/assignments it had created were
    left behind (`_reconcile_glance_rows` only ever looks at rows tied to
    *this* document's own id, so it can never see a row still pointing at a
    document that no longer exists at all). A student ended up seeing two
    conflicting sets of class days for the same two weeks. A fresh, healthy
    sync of the surviving document must sweep up those orphans too."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")

    # Rows from "doc-old", a document that has since been deleted entirely.
    fake_db.tables["calendar_events"].append({
        "id": "orphan-event", "user_id": USER_ID, "course_id": COURSE_ID,
        "title": "Stale topic", "external_id": f"schoology:class:{COURSE_ID}:doc-old:2025-10-06",
        "metadata": {"detected_from": "glance_doc", "source_document_id": "doc-old"},
    })
    fake_db.tables["assignments"].append({
        "id": "orphan-assignment", "user_id": USER_ID, "course_id": COURSE_ID,
        "title": "Stale Quiz", "external_source": "schoology",
        "external_id": f"schoology:glance-assignment:{COURSE_ID}:doc-old:2025-10-06:stale quiz",
        "metadata": {"detected_from": "glance_doc", "source_document_id": "doc-old"},
    })
    # "doc-1" is the only document row that actually still exists.
    fake_db.tables["documents"].append({"id": "doc-1", "metadata": {}})

    async def _fake(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": [
            {"title": "Lab Report", "due_date": "2025-10-09", "category": "lab"},
        ]}]}

    monkeypatch.setattr(claude, "complete_json", _fake)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit at a Glance",
        text="whatever", source="schoology", source_document_id="doc-1",
    ))

    event_ids = {e["id"] for e in fake_db.tables["calendar_events"]}
    assignment_ids = {a["id"] for a in fake_db.tables["assignments"]}
    assert "orphan-event" not in event_ids
    assert "orphan-assignment" not in assignment_ids
    # The current document's own fresh rows are untouched.
    assert any(e["external_id"].endswith(":doc-1:2025-10-06") for e in fake_db.tables["calendar_events"])
    assert any(a["title"] == "Lab Report" for a in fake_db.tables["assignments"])


def test_apply_schedule_from_doc_does_not_purge_rows_whose_document_still_exists(fake_db, monkeypatch):
    """Two different glance documents for the same course (e.g. two
    different units, each still a real document row) must never have their
    rows purged just because they weren't touched by this run's own
    extraction -- only a row whose *document* is actually gone qualifies."""
    from app.config import settings
    from app.llm import claude

    monkeypatch.setattr(settings, "groq_api_key", "fake-key")

    fake_db.tables["documents"].append({"id": "doc-unit1", "metadata": {}})
    fake_db.tables["documents"].append({"id": "doc-unit2", "metadata": {}})
    fake_db.tables["calendar_events"].append({
        "id": "unit1-event", "user_id": USER_ID, "course_id": COURSE_ID,
        "title": "Unit 1 topic", "external_id": f"schoology:class:{COURSE_ID}:doc-unit1:2025-09-01",
        "metadata": {"detected_from": "glance_doc", "source_document_id": "doc-unit1"},
    })

    async def _fake(*, system, prompt, max_tokens, temperature=0.0, fast=False, model=None):
        return {"days": [{"date": "2025-10-06", "topic": "Intro", "assignments": []}]}

    monkeypatch.setattr(claude, "complete_json", _fake)
    asyncio.run(schedule_extraction.apply_schedule_from_doc(
        user_id=USER_ID, course_id=COURSE_ID, title="Unit at a Glance",
        text="whatever", source="schoology", source_document_id="doc-unit2",
    ))

    event_ids = {e["id"] for e in fake_db.tables["calendar_events"]}
    assert "unit1-event" in event_ids  # its document still exists -- must survive


def test_glance_still_relevant_true_with_no_recorded_range():
    assert schedule_extraction.glance_still_relevant(None) is True
    assert schedule_extraction.glance_still_relevant({}) is True


def test_glance_still_relevant_true_within_range_and_grace_window():
    today = date.today().isoformat()
    assert schedule_extraction.glance_still_relevant(
        {"glance_date_range": {"start": today, "end": today}}
    ) is True


def test_glance_still_relevant_false_once_past_the_grace_window():
    long_ago = (date.today() - timedelta(days=30)).isoformat()
    assert schedule_extraction.glance_still_relevant(
        {"glance_date_range": {"start": long_ago, "end": long_ago}}
    ) is False
