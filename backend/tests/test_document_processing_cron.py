"""The document-processing cron must not let two overlapping runs both
process the same pending document — same "claim via a conditional UPDATE"
pattern as app.integrations._claim (see test_run_sync_overlap.py), just for
documents.ingested instead of integrations.status. Indexing/enrichment
moved out of the upload request (and out of a FastAPI BackgroundTask —
unreliable on Cloud Run, which freezes a container's CPU once its response
is sent) into this cron specifically so it always gets real CPU.
"""
from __future__ import annotations

import asyncio
import uuid

import app.routers.documents as documents_module

USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())


def test_claim_document_wins_when_unclaimed(monkeypatch):
    async def _fake_update(table, patch, *, filters):
        assert filters["id"] == f"eq.{DOC_ID}"
        assert "processing_started_at" in patch
        return [{"id": DOC_ID}]  # matched — claim won

    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)

    assert asyncio.run(documents_module._claim_document(DOC_ID)) is True


def test_claim_document_loses_when_already_freshly_claimed(monkeypatch):
    async def _fake_update(table, patch, *, filters):
        return []  # nothing matched — another run's claim is still fresh

    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)

    assert asyncio.run(documents_module._claim_document(DOC_ID)) is False


def test_process_pending_documents_claims_and_processes(monkeypatch):
    candidate = {
        "id": DOC_ID, "user_id": USER_ID, "storage_path": f"{USER_ID}/{DOC_ID}/notes.pdf",
        "mime_type": "application/pdf", "enrich": True, "auto_title": False,
    }
    update_calls = []

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [candidate]

    async def _fake_update(table, patch, *, filters):
        update_calls.append((patch, filters))
        return [{"id": DOC_ID}]  # claim always wins here

    async def _fake_download(key):
        assert key == candidate["storage_path"]
        return b"%PDF-1.4 fake bytes"

    async def _fake_ingest_document(doc_id, user_id, text):
        assert doc_id == DOC_ID
        assert user_id == USER_ID
        return {"chunks": 1}

    async def _fake_enrich(self, user_id, doc_id, text, *, rename_untitled=False):
        return {"summary": "ok"}

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)
    monkeypatch.setattr(documents_module.r2, "download", _fake_download)
    monkeypatch.setattr(documents_module.ingestion, "extract_text", lambda *a, **k: "some extracted text")
    monkeypatch.setattr(documents_module.ingestion, "ingest_document", _fake_ingest_document)
    monkeypatch.setattr(documents_module.settings, "groq_api_key", "fake-key")  # settings.has_llm -> True
    monkeypatch.setattr(documents_module.Archivist, "enrich", _fake_enrich)

    result = asyncio.run(documents_module._process_pending_documents())

    assert result == {"checked": 1, "processed": 1, "skipped": 0, "errors": 0}
    # The claim itself was the conditional UPDATE — never the final "mark it
    # ingested" write, which ingestion.ingest_document (faked above) owns.
    claim_calls = [c for c in update_calls if "processing_started_at" in c[0]]
    assert len(claim_calls) == 1


def test_process_pending_documents_skips_a_document_it_cant_claim(monkeypatch):
    candidate = {
        "id": DOC_ID, "user_id": USER_ID, "storage_path": f"{USER_ID}/{DOC_ID}/notes.pdf",
        "mime_type": "application/pdf", "enrich": True, "auto_title": False,
    }

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [candidate]

    async def _fake_update(table, patch, *, filters):
        return []  # another run already holds a fresh claim on it

    downloaded = False

    async def _fake_download(key):
        nonlocal downloaded
        downloaded = True
        return b""

    monkeypatch.setattr(documents_module.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)
    monkeypatch.setattr(documents_module.r2, "download", _fake_download)

    result = asyncio.run(documents_module._process_pending_documents())

    assert result == {"checked": 1, "processed": 0, "skipped": 1, "errors": 0}
    assert downloaded is False  # never even tried — the claim lost first


def test_process_document_flags_an_error_when_the_original_was_never_stored(monkeypatch):
    updates = []

    async def _fake_update(table, patch, *, filters):
        updates.append(patch)
        return [{"id": DOC_ID}]

    monkeypatch.setattr(documents_module.supabase, "update", _fake_update)

    asyncio.run(documents_module._process_document(
        DOC_ID, USER_ID, None, "application/pdf", enrich=True, auto_title=False,
    ))

    assert updates[-1]["ingested"] is False
    assert "never stored" in updates[-1]["ingest_error"]
