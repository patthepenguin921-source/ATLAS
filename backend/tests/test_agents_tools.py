"""`app.agents.tools` -- the concrete actions the chat agent can actually
perform ("add this as an assignment", "resync that document", "delete
this"). The critical property under test: a destructive tool
(delete_assignment/document/calendar_event) is *never* actually performed
by `execute_tool_for_chat` (the only entry point the model itself can
reach) -- only `confirm_tool` (reached exclusively via a dedicated,
person-clicked confirm step) can.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

import app.agents.tools as tools_module
import app.routers.documents as documents_module
from app.core.supabase_client import supabase

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "courses": [], "assignments": [], "documents": [], "calendar_events": [],
        }
        self.deleted: list[tuple[str, dict]] = []

    @staticmethod
    def _match(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if isinstance(v, str) and v.startswith("ilike.*") and v.endswith("*"):
                needle = v[len("ilike.*"):-1].lower()
                if needle not in (row.get(k) or "").lower():
                    return False
                continue
            if str(row.get(k)) != str(want):
                return False
        return True

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [r for r in self.tables.setdefault(table, []) if self._match(r, filters)][: limit or None]

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        rows = [rows] if isinstance(rows, dict) else rows
        out = []
        for r in rows:
            row = dict(r)
            row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)
            out.append(row)
        return out

    async def delete(self, table, *, filters):
        keep, removed = [], []
        for row in self.tables.setdefault(table, []):
            (removed if self._match(row, filters) else keep).append(row)
        self.tables[table] = keep
        self.deleted.append((table, filters))
        return removed


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase()
    for name in ("select", "insert", "delete"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    fake.tables["courses"].append({"id": COURSE_ID, "user_id": USER_ID, "name": "AP Biology"})
    return fake


def test_add_assignment_creates_a_row_scoped_to_the_matched_course(fake_db):
    result = asyncio.run(tools_module.execute_tool_for_chat(
        USER_ID, "add_assignment",
        {"title": "Lab Report", "course_name": "ap bio", "due_date": "2026-09-01"},
    ))

    assert result["status"] == "done"
    assert result["assignment"]["course_id"] == COURSE_ID
    stored = fake_db.tables["assignments"][0]
    assert stored["title"] == "Lab Report"
    assert stored["due_date"] == "2026-09-01"
    assert stored["category"] == "other"  # default when not specified


def test_add_assignment_requires_a_title(fake_db):
    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "add_assignment", {}))

    assert result["status"] == "error"
    assert fake_db.tables["assignments"] == []


def test_add_assignment_reports_an_unmatched_course_instead_of_guessing(fake_db):
    result = asyncio.run(tools_module.execute_tool_for_chat(
        USER_ID, "add_assignment", {"title": "Essay", "course_name": "Quantum Mechanics 401"},
    ))

    assert result["status"] == "error"
    assert "AP Biology" in result["message"]  # names the real classes instead
    assert fake_db.tables["assignments"] == []


def test_add_calendar_event_defaults_to_all_day(fake_db):
    result = asyncio.run(tools_module.execute_tool_for_chat(
        USER_ID, "add_calendar_event", {"title": "Field trip", "date": "2026-10-01"},
    ))

    assert result["status"] == "done"
    stored = fake_db.tables["calendar_events"][0]
    assert stored["all_day"] is True
    assert stored["starts_at"] == "2026-10-01"


def test_add_calendar_event_with_a_time_is_not_all_day(fake_db):
    asyncio.run(tools_module.execute_tool_for_chat(
        USER_ID, "add_calendar_event",
        {"title": "Parent meeting", "date": "2026-10-01", "start_time": "15:30"},
    ))

    stored = fake_db.tables["calendar_events"][0]
    assert stored["all_day"] is False
    assert stored["starts_at"] == "2026-10-01T15:30:00"


def test_resync_document_delegates_to_the_shared_core_function(fake_db, monkeypatch):
    doc_id = str(uuid.uuid4())
    fake_db.tables["documents"].append({
        "id": doc_id, "user_id": USER_ID, "title": "Unit 1 at a Glance", "course_id": COURSE_ID,
    })

    async def _fake_resync_core(user_id, document_id):
        assert user_id == USER_ID
        assert document_id == doc_id
        return {"ok": True, "id": document_id, "changed": True}

    monkeypatch.setattr(documents_module, "resync_document_core", _fake_resync_core)

    result = asyncio.run(tools_module.execute_tool_for_chat(
        USER_ID, "resync_document", {"document_title": "at a glance"},
    ))

    assert result["status"] == "done"
    assert "Unit 1 at a Glance" in result["message"]


def test_resync_document_surfaces_a_core_failure_as_an_error(fake_db, monkeypatch):
    doc_id = str(uuid.uuid4())
    fake_db.tables["documents"].append({"id": doc_id, "user_id": USER_ID, "title": "Syllabus", "course_id": COURSE_ID})

    async def _fake_resync_core(user_id, document_id):
        return {"ok": False, "message": "Connect Google Drive to let Atlas re-fetch this document."}

    monkeypatch.setattr(documents_module, "resync_document_core", _fake_resync_core)

    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "resync_document", {"document_title": "Syllabus"}))

    assert result["status"] == "error"
    assert "Google Drive" in result["message"]


def test_delete_assignment_is_only_ever_proposed_by_the_chat_entry_point(fake_db):
    fake_db.tables["assignments"].append({
        "id": "a1", "user_id": USER_ID, "title": "Old Homework", "course_id": COURSE_ID, "due_date": None,
    })

    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "delete_assignment", {"title": "Old Homework"}))

    assert result["status"] == "pending_confirmation"
    assert result["action"] == {"name": "delete_assignment", "arguments": {"id": "a1"}}
    # Nothing was actually deleted.
    assert len(fake_db.tables["assignments"]) == 1
    assert fake_db.deleted == []


def test_confirm_tool_actually_deletes_using_the_resolved_id(fake_db):
    fake_db.tables["assignments"].append({
        "id": "a1", "user_id": USER_ID, "title": "Old Homework", "course_id": COURSE_ID, "due_date": None,
    })

    result = asyncio.run(tools_module.confirm_tool(USER_ID, "delete_assignment", {"id": "a1"}))

    assert result["status"] == "done"
    assert fake_db.tables["assignments"] == []


def test_execute_tool_for_chat_never_deletes_even_if_called_with_an_id(fake_db):
    """Defense in depth: even if a model somehow produced an `id` argument
    directly (it's never told any row's id), the chat-facing entry point
    must still only propose, never perform, a destructive action."""
    fake_db.tables["assignments"].append({
        "id": "a1", "user_id": USER_ID, "title": "Old Homework", "course_id": COURSE_ID, "due_date": None,
    })

    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "delete_assignment", {"id": "a1"}))

    assert result["status"] == "pending_confirmation"
    assert len(fake_db.tables["assignments"]) == 1


def test_delete_document_confirmation_queues_storage_cleanup(fake_db, monkeypatch):
    fake_db.tables["documents"].append({
        "id": "d1", "user_id": USER_ID, "title": "Old Notes", "course_id": COURSE_ID,
        "storage_path": f"{USER_ID}/d1/notes.pdf",
    })
    queued = []

    async def _fake_queue_deletion(path):
        queued.append(path)

    from app.services import storage_cleanup
    monkeypatch.setattr(storage_cleanup, "queue_deletion", _fake_queue_deletion)

    proposal = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "delete_document", {"title": "Old Notes"}))
    assert proposal["status"] == "pending_confirmation"

    result = asyncio.run(tools_module.confirm_tool(USER_ID, "delete_document", proposal["action"]["arguments"]))

    assert result["status"] == "done"
    assert fake_db.tables["documents"] == []
    assert queued == [f"{USER_ID}/d1/notes.pdf"]


def test_ambiguous_title_match_lists_candidates_instead_of_guessing(fake_db):
    fake_db.tables["assignments"].append({"id": "a1", "user_id": USER_ID, "title": "Quiz", "course_id": COURSE_ID, "due_date": None})
    fake_db.tables["assignments"].append({"id": "a2", "user_id": USER_ID, "title": "Quiz", "course_id": COURSE_ID, "due_date": None})

    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "delete_assignment", {"title": "Quiz"}))

    assert result["status"] == "error"
    assert result["error"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert len(fake_db.tables["assignments"]) == 2


def test_not_found_title_reports_an_error(fake_db):
    result = asyncio.run(tools_module.execute_tool_for_chat(USER_ID, "delete_calendar_event", {"title": "Nonexistent"}))

    assert result["status"] == "error"
    assert result["error"] == "not_found"
