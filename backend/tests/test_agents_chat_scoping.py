"""New conversation/course_id + project-instructions wiring in
`app.routers.agents`: `_persist_turn` stamps a brand-new conversation with
whatever class scope was active, `_get_project_instructions` looks up a
project's custom instructions for an existing conversation, and the
conversation PATCH endpoint accepts the two new fields a student can set
themselves (`pinned`, `course_id`).
"""
from __future__ import annotations

import asyncio
import uuid

import app.routers.agents as agents_module
from app.core.supabase_client import supabase

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
CONV_ID = str(uuid.uuid4())


def test_persist_turn_stamps_a_new_conversation_with_the_active_scope(monkeypatch):
    inserted = []

    async def _fake_insert(table, rows, **kwargs):
        inserted.append((table, rows))
        if table == "conversations":
            return [{"id": CONV_ID}]
        return [{"id": "m1"}, {"id": "m2"}]

    async def _fake_update(table, patch, *, filters):
        return [{}]

    monkeypatch.setattr(supabase, "insert", _fake_insert)
    monkeypatch.setattr(supabase, "update", _fake_update)

    conv_id, msg_id = asyncio.run(agents_module._persist_turn(
        USER_ID, None, "general", "hi", "hello!", {}, course_id=COURSE_ID,
    ))

    assert conv_id == CONV_ID
    conv_table, conv_rows = inserted[0]
    assert conv_table == "conversations"
    assert conv_rows["course_id"] == COURSE_ID


def test_persist_turn_never_re_inserts_an_existing_conversation(monkeypatch):
    tables_inserted = []

    async def _fake_insert(table, rows, **kwargs):
        tables_inserted.append(table)
        return [{"id": "m1"}, {"id": "m2"}]

    async def _fake_update(table, patch, *, filters):
        return [{}]

    monkeypatch.setattr(supabase, "insert", _fake_insert)
    monkeypatch.setattr(supabase, "update", _fake_update)

    conv_id, _ = asyncio.run(agents_module._persist_turn(
        USER_ID, CONV_ID, "general", "hi", "hello!", {}, course_id=COURSE_ID,
    ))

    assert conv_id == CONV_ID
    assert "conversations" not in tables_inserted  # it already existed -- no insert


def test_get_project_instructions_looks_up_the_conversation_then_the_project(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        if table == "conversations":
            return [{"project_id": PROJECT_ID}]
        if table == "chat_projects":
            return [{"instructions": "Always show your work."}]
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(supabase, "select", _fake_select)

    result = asyncio.run(agents_module._get_project_instructions(USER_ID, CONV_ID))

    assert result == "Always show your work."


def test_get_project_instructions_is_none_for_a_brand_new_conversation():
    # No conversation_id yet -- nothing to look up, and no supabase call
    # should even be attempted.
    assert asyncio.run(agents_module._get_project_instructions(USER_ID, None)) is None


def test_get_project_instructions_is_none_when_not_filed_in_a_project(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "conversations"
        return [{"project_id": None}]

    monkeypatch.setattr(supabase, "select", _fake_select)

    assert asyncio.run(agents_module._get_project_instructions(USER_ID, CONV_ID)) is None


def test_conversation_patch_whitelist_includes_pin_and_course_id():
    # A student re-filing a chat that auto-sorted into the wrong class, or
    # pinning one, both go through this same generic PATCH.
    assert {"pinned", "course_id", "project_id", "title", "tags", "archived"} <= agents_module._CONV_WRITABLE
