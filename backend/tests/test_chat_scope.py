"""`app.agents.chat_scope.maybe_classify_scope` -- auto-sorts a brand-new,
unscoped conversation into the class it's actually about. Must never
reconsider a conversation that's already scoped (explicitly or by a prior
pass), never guess when the model says no single class fits, and never let
a classification failure raise out of the caller (chat() calls this
best-effort, after the turn already succeeded and got a reply)."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.agents import chat_scope
from app.core.supabase_client import supabase
from app.llm import claude

USER_ID = str(uuid.uuid4())
CONV_ID = str(uuid.uuid4())
BIO_ID = str(uuid.uuid4())
CALC_ID = str(uuid.uuid4())


class FakeSupabase:
    def __init__(self, *, conversation_course_id: str | None, courses: list[dict[str, Any]]) -> None:
        self.conversation_course_id = conversation_course_id
        self.courses = courses
        self.updates: list[dict[str, Any]] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        if table == "conversations":
            return [{"course_id": self.conversation_course_id}]
        if table == "courses":
            return list(self.courses)
        raise AssertionError(f"unexpected table {table}")

    async def update(self, table, patch, *, filters):
        assert table == "conversations"
        self.updates.append(patch)
        return [{"id": CONV_ID, **patch}]


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase(
        conversation_course_id=None,
        courses=[{"id": BIO_ID, "name": "AP Biology"}, {"id": CALC_ID, "name": "AP Calculus"}],
    )
    monkeypatch.setattr(supabase, "select", fake.select)
    monkeypatch.setattr(supabase, "update", fake.update)
    return fake


def test_classifies_and_persists_a_confident_match(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        assert "AP Biology" in prompt and "AP Calculus" in prompt
        return {"course_name": "AP Biology"}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "What's on the AP Bio unit 3 test?", "It covers cellular respiration.",
    ))

    assert fake_db.updates == [{"course_id": BIO_ID}]


def test_direct_mention_classifies_without_ever_calling_the_model(fake_db, monkeypatch):
    called = False

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        nonlocal called
        called = True
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "What's due in AP Biology this week?", "The lab report.",
    ))

    assert fake_db.updates == [{"course_id": BIO_ID}]
    assert not called  # the literal name match never needed the model at all


def test_direct_mention_also_checks_atlas_own_reply(fake_db, monkeypatch):
    """The student's phrasing can be vague ("what's due this week") while
    Atlas's grounded answer names the class directly -- that should still
    count as a mention."""
    called = False

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        nonlocal called
        called = True
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "what's due this week", "Your AP Biology lab report is due Friday.",
    ))

    assert fake_db.updates == [{"course_id": BIO_ID}]
    assert not called


def test_direct_mention_is_word_boundary_matched(fake_db, monkeypatch):
    """A course named "AP Biology" (multi-word) is used elsewhere in these
    tests; here a short single-word course name must not fire on a
    substring of some other word."""
    fake_db.courses.append({"id": "art-id", "name": "Art"})

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "Let's start the AP Bio review now.", "Sure thing.",
    ))

    # "start" contains "art" as a substring but not as a whole word -- must
    # not have matched the "Art" course.
    assert fake_db.updates == []


def test_direct_mention_word_boundary_positive_match(fake_db, monkeypatch):
    fake_db.courses.append({"id": "art-id", "name": "Art"})
    called = False

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        nonlocal called
        called = True
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "What's the Art project due date?", "Friday.",
    ))

    assert fake_db.updates == [{"course_id": "art-id"}]
    assert not called


def test_multiple_direct_mentions_fall_back_to_the_model_instead_of_guessing(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "Is AP Biology harder than AP Calculus?", "Depends on the student.",
    ))

    assert fake_db.updates == []


def test_allow_llm_fallback_false_skips_the_model_when_nothing_is_named_directly(fake_db, monkeypatch):
    called = False

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        nonlocal called
        called = True
        return {"course_name": "AP Biology"}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(
        USER_ID, CONV_ID, "what's on my plate today", "Not much.", allow_llm_fallback=False,
    ))

    assert not called
    assert fake_db.updates == []


def test_does_nothing_when_the_model_finds_no_single_class(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return {"course_name": None}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(USER_ID, CONV_ID, "hey what's up", "Not much!"))

    assert fake_db.updates == []


def test_never_reconsiders_an_already_scoped_conversation(monkeypatch):
    fake = FakeSupabase(conversation_course_id=BIO_ID, courses=[{"id": BIO_ID, "name": "AP Biology"}])
    monkeypatch.setattr(supabase, "select", fake.select)
    monkeypatch.setattr(supabase, "update", fake.update)

    called = False

    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        nonlocal called
        called = True
        return {"course_name": "AP Biology"}

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(USER_ID, CONV_ID, "anything", "anything"))

    assert not called  # never even asks the model once a scope is already set
    assert fake.updates == []


def test_ignores_a_course_name_the_model_hallucinated(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        return {"course_name": "Quantum Mechanics 401"}  # not one of the student's real classes

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    asyncio.run(chat_scope.maybe_classify_scope(USER_ID, CONV_ID, "x", "y"))

    assert fake_db.updates == []


def test_swallows_a_classifier_failure(fake_db, monkeypatch):
    async def _fake_complete_json(*, system, prompt, max_tokens, fast=False):
        raise RuntimeError("LLM provider error")

    monkeypatch.setattr(claude, "complete_json", _fake_complete_json)

    # Must not raise -- chat() calls this after the reply already succeeded.
    asyncio.run(chat_scope.maybe_classify_scope(USER_ID, CONV_ID, "x", "y"))

    assert fake_db.updates == []
