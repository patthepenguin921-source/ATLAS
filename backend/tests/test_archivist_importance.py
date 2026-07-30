"""Archivist.enrich infers an importance rating alongside the existing
summary/keywords/doc_type — but must never clobber a rating the student set
manually (`importance_source = "manual"`, set by `update_document` on any
PATCH that includes `importance`).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.agents import archivist as archivist_module
from app.agents.archivist import Archivist
from app.core.supabase_client import supabase
from app.llm import claude

USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())

_ENRICH_RESULT = {
    "title": "Some Title",
    "summary": "A summary.",
    "keywords": ["a", "b"],
    "doc_type": "notes",
    "importance": "high",
    "concepts": [],
}


def _install_fakes(
    monkeypatch, *,
    existing_importance_source: str | None = None,
    existing_doc_type_source: str | None = None,
):
    updates: list[dict[str, Any]] = []

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return dict(_ENRICH_RESULT)

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "documents"
        assert columns == "importance_source,doc_type_source,folder_source,course_id"
        return [{
            "importance_source": existing_importance_source,
            "doc_type_source": existing_doc_type_source,
            "folder_source": None,
            "course_id": COURSE_ID,
        }]

    async def _fake_update(table, patch, *, filters):
        updates.append(patch)
        return [{"id": DOC_ID, **patch}]

    # Folder auto-sort is exercised separately (see test_archivist_folder_sort.py) --
    # here it's a no-op so these tests stay focused on importance/doc_type.
    async def _fake_top_level_folders(user_id, *, course_id):
        return [], None

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)
    monkeypatch.setattr(supabase, "select", _fake_select)
    monkeypatch.setattr(supabase, "update", _fake_update)
    monkeypatch.setattr(archivist_module, "top_level_folders", _fake_top_level_folders)
    return updates


def test_enrich_sets_ai_importance_when_nothing_set_before(monkeypatch):
    updates = _install_fakes(monkeypatch, existing_importance_source=None)

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert updates[0]["importance"] == "high"
    assert updates[0]["importance_source"] == "ai"


def test_enrich_overwrites_a_previous_ai_rating(monkeypatch):
    updates = _install_fakes(monkeypatch, existing_importance_source="ai")

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert updates[0]["importance"] == "high"
    assert updates[0]["importance_source"] == "ai"


def test_enrich_never_overwrites_a_manual_rating(monkeypatch):
    updates = _install_fakes(monkeypatch, existing_importance_source="manual")

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert "importance" not in updates[0]
    assert "importance_source" not in updates[0]


def test_enrich_sets_ai_doc_type_when_nothing_set_before(monkeypatch):
    updates = _install_fakes(monkeypatch, existing_doc_type_source=None)

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert updates[0]["doc_type"] == "notes"
    assert updates[0]["doc_type_source"] == "ai"


def test_enrich_never_overwrites_a_manual_doc_type(monkeypatch):
    updates = _install_fakes(monkeypatch, existing_doc_type_source="manual")

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert "doc_type" not in updates[0]
    assert "doc_type_source" not in updates[0]


def test_enrich_never_overwrites_a_system_set_glance_tag(monkeypatch):
    """A `glance` tag set with certainty by
    `schedule_extraction._tag_as_glance` must survive a later re-enrichment
    the same way a manual re-tag does -- the general enrichment pass is only
    ever guessing from content, and shouldn't second-guess something Atlas
    already knows for a fact."""
    updates = _install_fakes(monkeypatch, existing_doc_type_source="system")

    asyncio.run(Archivist().enrich(USER_ID, DOC_ID, "some document text"))

    assert "doc_type" not in updates[0]
    assert "doc_type_source" not in updates[0]
