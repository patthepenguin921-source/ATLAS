"""Archivist.enrich infers an importance rating alongside the existing
summary/keywords/doc_type — but must never clobber a rating the student set
manually (`importance_source = "manual"`, set by `update_document` on any
PATCH that includes `importance`).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.agents.archivist import Archivist
from app.core.supabase_client import supabase
from app.llm import claude

USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())

_ENRICH_RESULT = {
    "title": "Some Title",
    "summary": "A summary.",
    "keywords": ["a", "b"],
    "doc_type": "notes",
    "importance": "high",
    "concepts": [],
}


def _install_fakes(monkeypatch, *, existing_importance_source: str | None):
    updates: list[dict[str, Any]] = []

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return dict(_ENRICH_RESULT)

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "documents"
        assert columns == "importance_source"
        return [{"importance_source": existing_importance_source}]

    async def _fake_update(table, patch, *, filters):
        updates.append(patch)
        return [{"id": DOC_ID, **patch}]

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)
    monkeypatch.setattr(supabase, "select", _fake_select)
    monkeypatch.setattr(supabase, "update", _fake_update)
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
