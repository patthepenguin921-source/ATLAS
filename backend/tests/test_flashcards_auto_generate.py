"""`app.services.flashcards.maybe_auto_generate` -- generates a small
starter flashcard deck automatically right after a document finishes
indexing, instead of requiring the student to notice a new document and
click "Generate flashcards" by hand for every single file."""
from __future__ import annotations

import asyncio
import uuid

import app.services.flashcards as flashcards

USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())


class _FakeSupabase:
    def __init__(self, existing_flashcards=None, document_concepts=None):
        self.existing_flashcards = existing_flashcards or []
        self.document_concepts = document_concepts or []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        if table == "flashcards":
            return self.existing_flashcards
        if table == "document_concepts":
            return self.document_concepts
        return []


def _install(monkeypatch, *, existing_flashcards=None, document_concepts=None):
    fake = _FakeSupabase(existing_flashcards=existing_flashcards, document_concepts=document_concepts)
    monkeypatch.setattr(flashcards, "supabase", fake)
    return fake


def test_generates_when_document_has_concepts_and_no_existing_cards(monkeypatch):
    _install(monkeypatch, document_concepts=[{"concept_id": "c1"}])
    called = []

    async def fake_generate(user_id, *, document_id=None, course_id=None, folder_id=None, max_cards=15):
        called.append((document_id, max_cards))
        return {"status": "done", "count": 3}

    monkeypatch.setattr(flashcards, "generate", fake_generate)

    asyncio.run(flashcards.maybe_auto_generate(USER_ID, DOC_ID))

    assert called == [(DOC_ID, flashcards._AUTO_GENERATE_MAX_CARDS)]


def test_skips_when_flashcards_already_exist_for_the_document(monkeypatch):
    _install(monkeypatch, existing_flashcards=[{"id": "fc1"}], document_concepts=[{"concept_id": "c1"}])
    called = []

    async def fake_generate(*args, **kwargs):
        called.append(True)

    monkeypatch.setattr(flashcards, "generate", fake_generate)

    asyncio.run(flashcards.maybe_auto_generate(USER_ID, DOC_ID))

    assert not called


def test_skips_when_document_has_no_extracted_concepts(monkeypatch):
    """An announcement, rubric, or personal note rarely gets tagged with a
    concept -- this is what keeps auto-generation from firing on those."""
    _install(monkeypatch, document_concepts=[])
    called = []

    async def fake_generate(*args, **kwargs):
        called.append(True)

    monkeypatch.setattr(flashcards, "generate", fake_generate)

    asyncio.run(flashcards.maybe_auto_generate(USER_ID, DOC_ID))

    assert not called


def test_never_raises_when_generate_itself_fails(monkeypatch):
    _install(monkeypatch, document_concepts=[{"concept_id": "c1"}])

    async def failing_generate(*args, **kwargs):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(flashcards, "generate", failing_generate)

    asyncio.run(flashcards.maybe_auto_generate(USER_ID, DOC_ID))  # must not raise


def test_never_raises_when_supabase_lookups_fail(monkeypatch):
    class _BoomSupabase:
        async def select(self, *args, **kwargs):
            raise RuntimeError("db exploded")

    monkeypatch.setattr(flashcards, "supabase", _BoomSupabase())

    asyncio.run(flashcards.maybe_auto_generate(USER_ID, DOC_ID))  # must not raise
