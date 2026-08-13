"""`app.services.document_dedupe` -- possible-duplicate detection and the
merge/dismiss actions the documents page's review section uses. Mirrors
`test_assignment_dedupe.py`'s structure for the assignment-merge analog."""
from __future__ import annotations

import asyncio
import uuid

import app.services.document_dedupe as dedupe

USER_ID = str(uuid.uuid4())
COURSE_A = str(uuid.uuid4())
COURSE_B = str(uuid.uuid4())


def _document(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "title": "Unit 3 Slideshow",
        "course_id": COURSE_A,
        "folder_id": None,
        "assignment_id": None,
        "doc_type": None,
        "summary": None,
        "keywords": None,
        "tags": None,
        "importance": None,
        "page_count": None,
        "size_bytes": 1000,
        "ingested": True,
        "storage_path": "user/doc/file.pdf",
        "created_at": "2026-03-01T00:00:00Z",
    }
    base.update(overrides)
    return base


class _FakeSupabase:
    """Stands in for `app.core.supabase_client.supabase` -- records every
    call so tests can assert on exactly what was sent, without touching a
    real database."""

    def __init__(self, documents=None, chunks=None, concepts=None, dismissals=None, calendar_events=None):
        self.documents = list(documents or [])
        self.chunks = list(chunks or [])
        self.concepts = list(concepts or [])
        self.dismissals = list(dismissals or [])
        self.calendar_events = list(calendar_events or [])
        self.updates: list[dict] = []
        self.deletes: list[dict] = []
        self.inserts: list[dict] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        filters = filters or {}
        if table == "documents":
            rows = self.documents
            if "id" in filters and filters["id"].startswith("in."):
                ids = filters["id"][len("in.("):-1].split(",")
                rows = [d for d in rows if d["id"] in ids]
            return rows
        if table == "document_chunks":
            doc_id = filters.get("document_id", "").removeprefix("eq.")
            rows = [c for c in self.chunks if c["document_id"] == doc_id]
            if order == "chunk_index.desc":
                rows = sorted(rows, key=lambda c: c["chunk_index"], reverse=True)
            elif order == "chunk_index.asc":
                rows = sorted(rows, key=lambda c: c["chunk_index"])
            return rows[:limit] if limit else rows
        if table == "document_concepts":
            doc_id = filters.get("document_id", "").removeprefix("eq.")
            return [c for c in self.concepts if c["document_id"] == doc_id]
        if table == "document_duplicate_dismissals":
            return self.dismissals
        if table == "calendar_events":
            target = filters.get("metadata->>source_document_id", "").removeprefix("eq.")
            return [
                e for e in self.calendar_events
                if (e.get("metadata") or {}).get("source_document_id") == target
            ]
        return []

    async def update(self, table, patch, *, filters):
        self.updates.append({"table": table, "patch": patch, "filters": filters})
        if table == "document_chunks":
            doc_id = filters.get("id", "").removeprefix("eq.")
            for c in self.chunks:
                if c["id"] == doc_id:
                    c.update(patch)
        if table == "document_concepts":
            doc_id = filters.get("document_id", "").removeprefix("eq.")
            concept_id = filters.get("concept_id", "").removeprefix("eq.")
            for c in self.concepts:
                if c["document_id"] == doc_id and c["concept_id"] == concept_id:
                    c.update(patch)
        return [patch]

    async def delete(self, table, *, filters):
        self.deletes.append({"table": table, "filters": filters})
        return None

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        self.inserts.append({"table": table, "rows": rows, "upsert": upsert, "on_conflict": on_conflict})
        return [rows]


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(dedupe, "supabase", fake)
    queued: list[str] = []

    async def fake_queue_deletion(storage_path):
        queued.append(storage_path)

    monkeypatch.setattr(dedupe.storage_cleanup, "queue_deletion", fake_queue_deletion)
    return fake, queued


# ---- find_possible_duplicates ------------------------------------------------

def test_flags_same_course_similar_title(monkeypatch):
    a = _document(title="Unit 3 Slideshow")
    b = _document(title="Unit 3 Slideshow ", id=str(uuid.uuid4()))
    _install(monkeypatch, documents=[a, b])

    result = asyncio.run(dedupe.find_possible_duplicates(USER_ID))

    assert len(result) == 1
    assert {a["id"], b["id"]} == {x["id"] for x in result[0]["documents"]}


def test_does_not_flag_different_courses(monkeypatch):
    a = _document(course_id=COURSE_A)
    b = _document(course_id=COURSE_B, id=str(uuid.uuid4()))
    _install(monkeypatch, documents=[a, b])

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_flags_both_unfiled_general_documents(monkeypatch):
    a = _document(course_id=None, title="Syllabus")
    b = _document(course_id=None, title="Syllabus", id=str(uuid.uuid4()))
    _install(monkeypatch, documents=[a, b])

    assert len(asyncio.run(dedupe.find_possible_duplicates(USER_ID))) == 1


def test_does_not_flag_dissimilar_titles(monkeypatch):
    a = _document(title="Unit 3 Slideshow")
    b = _document(title="Chapter 7 Reading", id=str(uuid.uuid4()))
    _install(monkeypatch, documents=[a, b])

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_excludes_pairs_already_dismissed(monkeypatch):
    a = _document(title="Unit 3 Slideshow")
    b = _document(title="Unit 3 Slideshow ", id=str(uuid.uuid4()))
    pair = sorted([a["id"], b["id"]])
    _install(
        monkeypatch, documents=[a, b],
        dismissals=[{"document_id_a": pair[0], "document_id_b": pair[1]}],
    )

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_suggests_keeping_the_ingested_document_over_the_unindexed_one(monkeypatch):
    pending = _document(title="Unit 3 Slideshow", ingested=False)
    indexed = _document(title="Unit 3 Slideshow ", id=str(uuid.uuid4()), ingested=True)
    _install(monkeypatch, documents=[pending, indexed])

    result = asyncio.run(dedupe.find_possible_duplicates(USER_ID))

    assert result[0]["suggested_keep_id"] == indexed["id"]
    assert result[0]["suggested_discard_id"] == pending["id"]


# ---- merge_documents -----------------------------------------------------------

def test_merge_renumbers_and_reassigns_chunks(monkeypatch):
    keep = _document()
    discard = _document(id=str(uuid.uuid4()))
    keep_chunks = [
        {"id": "kc0", "document_id": keep["id"], "chunk_index": 0},
        {"id": "kc1", "document_id": keep["id"], "chunk_index": 1},
    ]
    discard_chunks = [
        {"id": "dc0", "document_id": discard["id"], "chunk_index": 0},
        {"id": "dc1", "document_id": discard["id"], "chunk_index": 1},
    ]
    fake, _ = _install(monkeypatch, documents=[keep, discard], chunks=keep_chunks + discard_chunks)

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    chunk_updates = [u for u in fake.updates if u["table"] == "document_chunks"]
    assert len(chunk_updates) == 2
    new_indices = sorted(u["patch"]["chunk_index"] for u in chunk_updates)
    assert new_indices == [2, 3]  # continues right after keep's own max index (1)
    assert all(u["patch"]["document_id"] == keep["id"] for u in chunk_updates)


def test_merge_skips_concept_already_on_kept_document(monkeypatch):
    keep = _document()
    discard = _document(id=str(uuid.uuid4()))
    shared_concept = str(uuid.uuid4())
    new_concept = str(uuid.uuid4())
    concepts = [
        {"document_id": keep["id"], "concept_id": shared_concept},
        {"document_id": discard["id"], "concept_id": shared_concept},
        {"document_id": discard["id"], "concept_id": new_concept},
    ]
    fake, _ = _install(monkeypatch, documents=[keep, discard], concepts=concepts)

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    concept_updates = [u for u in fake.updates if u["table"] == "document_concepts"]
    assert len(concept_updates) == 1
    assert concept_updates[0]["filters"]["concept_id"] == f"eq.{new_concept}"
    assert concept_updates[0]["patch"]["document_id"] == keep["id"]


def test_merge_reassigns_flashcards(monkeypatch):
    keep = _document()
    discard = _document(id=str(uuid.uuid4()))
    fake, _ = _install(monkeypatch, documents=[keep, discard])

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    flashcard_update = next(u for u in fake.updates if u["table"] == "flashcards")
    assert flashcard_update["patch"] == {"document_id": keep["id"]}
    assert flashcard_update["filters"]["document_id"] == f"eq.{discard['id']}"


def test_merge_fills_blank_fields_and_ingested_flag(monkeypatch):
    keep = _document(summary=None, ingested=False)
    discard = _document(id=str(uuid.uuid4()), summary="A helpful summary.", ingested=True)
    fake, _ = _install(monkeypatch, documents=[keep, discard])

    result = asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    fill_update = next(u for u in fake.updates if u["table"] == "documents")
    assert fill_update["patch"]["summary"] == "A helpful summary."
    assert fill_update["patch"]["ingested"] is True
    assert result["summary"] == "A helpful summary."


def test_merge_does_not_overwrite_a_field_the_kept_row_already_has(monkeypatch):
    keep = _document(summary="Keep's own summary")
    discard = _document(id=str(uuid.uuid4()), summary="Discard's summary")
    fake, _ = _install(monkeypatch, documents=[keep, discard])

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    fill_update = next((u for u in fake.updates if u["table"] == "documents"), None)
    assert fill_update is None or "summary" not in fill_update["patch"]


def test_merge_deletes_discard_and_queues_its_file_for_removal(monkeypatch):
    keep = _document()
    discard = _document(id=str(uuid.uuid4()), storage_path="user/discard/file.pdf")
    fake, queued = _install(monkeypatch, documents=[keep, discard])

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    assert fake.deletes == [
        {"table": "documents", "filters": {"user_id": f"eq.{USER_ID}", "id": f"eq.{discard['id']}"}}
    ]
    assert queued == ["user/discard/file.pdf"]


def test_merge_rewrites_calendar_event_source_document_id(monkeypatch):
    keep = _document()
    discard = _document(id=str(uuid.uuid4()))
    events = [{"id": "evt-1", "metadata": {"source_document_id": discard["id"], "other": "x"}}]
    fake, _ = _install(monkeypatch, documents=[keep, discard], calendar_events=events)

    asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], discard["id"]))

    event_update = next(u for u in fake.updates if u["table"] == "calendar_events")
    assert event_update["patch"]["metadata"]["source_document_id"] == keep["id"]
    assert event_update["patch"]["metadata"]["other"] == "x"


def test_merge_rejects_merging_a_document_with_itself(monkeypatch):
    _install(monkeypatch)
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(dedupe.merge_documents(USER_ID, "same-id", "same-id"))


def test_merge_raises_when_a_document_is_not_found(monkeypatch):
    keep = _document()
    _install(monkeypatch, documents=[keep])
    import pytest
    with pytest.raises(LookupError):
        asyncio.run(dedupe.merge_documents(USER_ID, keep["id"], str(uuid.uuid4())))


# ---- dismiss_duplicate ---------------------------------------------------------

def test_dismiss_stores_ids_in_canonical_sorted_order(monkeypatch):
    fake, _ = _install(monkeypatch)
    high, low = "b-id", "a-id"

    asyncio.run(dedupe.dismiss_duplicate(USER_ID, high, low))

    assert fake.inserts[0]["rows"] == {
        "user_id": USER_ID, "document_id_a": "a-id", "document_id_b": "b-id",
    }
    assert fake.inserts[0]["upsert"] is True
