"""`POST /documents/{id}/resync` -- re-fetch and reprocess a single document
from its live Google Doc/Slides/Sheet source, without running a full
Schoology sync. Only documents linked via `metadata.source_url` to a Google
file are refetchable this way; anything else (a raw Schoology attachment)
gets a clear 422 explaining a full sync is still needed for it.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

import app.routers.documents as documents_module
from app.core.security import CurrentUser

USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())
USER = CurrentUser(id=USER_ID, email="student@example.com")

GOOGLE_URL = "https://docs.google.com/document/d/abc123/edit?usp=sharing"


def _doc_row(**overrides):
    row = {
        "id": DOC_ID, "user_id": USER_ID, "course_id": COURSE_ID, "title": "Unit at a Glance",
        "metadata": {"source_url": GOOGLE_URL, "content_hash": "old-hash", "is_glance": True},
        "auto_title": False,
    }
    row.update(overrides)
    return row


def test_resync_rejects_a_document_with_no_source_url(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [_doc_row(metadata={})]

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(documents_module.resync_document(DOC_ID, user=USER))
    assert exc.value.status_code == 422
    assert "full Schoology sync" in exc.value.detail


def test_resync_rejects_a_non_google_source_url(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [_doc_row(metadata={"source_url": "https://example.com/some-page"})]

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(documents_module.resync_document(DOC_ID, user=USER))
    assert exc.value.status_code == 422


def test_resync_requires_a_google_token(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [_doc_row()]

    async def _fake_token(self, user_id):
        return None

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_module.SchoologyProvider, "google_token_for_resync", _fake_token)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(documents_module.resync_document(DOC_ID, user=USER))
    assert exc.value.status_code == 422
    assert "Connect Google Drive" in exc.value.detail


def test_resync_is_a_noop_when_content_is_unchanged(monkeypatch):
    import hashlib
    content_hash = hashlib.sha256(b"same content").hexdigest()

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [_doc_row(metadata={"source_url": GOOGLE_URL, "content_hash": content_hash})]

    async def _fake_token(self, user_id):
        return "fake-access-token"

    async def _fake_download(ref, access_token, *, name="document"):
        return b"same content", "unit.pdf", "application/pdf"

    calls = {"ingest": 0}

    async def _fake_ingest_document(doc_id, user_id, text):
        calls["ingest"] += 1
        return {"chunks": 1}

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_module.SchoologyProvider, "google_token_for_resync", _fake_token)
    monkeypatch.setattr(documents_module.google_files, "download_google_file", _fake_download)
    monkeypatch.setattr(documents_module.ingestion, "ingest_document", _fake_ingest_document)

    result = asyncio.run(documents_module.resync_document(DOC_ID, user=USER))

    assert result == {"id": DOC_ID, "changed": False}
    assert calls["ingest"] == 0


def test_resync_reprocesses_changed_content_and_reruns_glance_schedule(monkeypatch):
    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [_doc_row()]  # content_hash "old-hash" -- new download differs

    async def _fake_token(self, user_id):
        return "fake-access-token"

    async def _fake_download(ref, access_token, *, name="document"):
        return b"brand new content", "unit.pdf", "application/pdf"

    updates = []

    async def _fake_update(table, patch, *, filters):
        updates.append(patch)
        return [{"id": DOC_ID, **patch}]

    async def _fake_upload(path, content, content_type):
        return None

    async def _fake_ingest_document(doc_id, user_id, text):
        assert doc_id == DOC_ID
        return {"chunks": 2}

    enrich_calls = []

    async def _fake_enrich_or_fallback(self, user_id, doc_id, text, *, rename_untitled=False, fallback_title=None):
        enrich_calls.append(doc_id)

    schedule_calls = []

    async def _fake_apply_schedule(*, user_id, course_id, title, text, source, source_document_id=None, report=None):
        schedule_calls.append(source_document_id)

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)
    monkeypatch.setattr(documents_module.SchoologyProvider, "google_token_for_resync", _fake_token)
    monkeypatch.setattr(documents_module.google_files, "download_google_file", _fake_download)
    monkeypatch.setattr(documents_module.r2, "upload", _fake_upload)
    monkeypatch.setattr(documents_module.ingestion, "extract_text",
                         lambda *a, **k: "# Unit at a Glance\n\nMonday 10/6: intro.")
    monkeypatch.setattr(documents_module.ingestion, "ingest_document", _fake_ingest_document)
    monkeypatch.setattr(documents_module.Archivist, "enrich_or_fallback", _fake_enrich_or_fallback)
    monkeypatch.setattr(documents_module, "apply_schedule_from_doc", _fake_apply_schedule)

    result = asyncio.run(documents_module.resync_document(DOC_ID, user=USER))

    assert result == {"id": DOC_ID, "changed": True}
    assert enrich_calls == [DOC_ID]
    assert schedule_calls == [DOC_ID]
    # content_hash actually persisted so a later resync with the same bytes is a no-op
    meta_updates = [p["metadata"] for p in updates if "metadata" in p]
    assert meta_updates
    import hashlib
    assert meta_updates[0]["content_hash"] == hashlib.sha256(b"brand new content").hexdigest()
