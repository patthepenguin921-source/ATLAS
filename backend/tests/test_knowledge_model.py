"""`app.services.knowledge_model` -- the SM-2 spaced-repetition engine and
`observe_grade`'s percentage->quality mapping.

Regression coverage for a rounding bug: Python's `round()` is round-half-to-
even, not round-half-up, and `percentage / 20` lands exactly on a `.5`
boundary for every multiple of 10 -- 70% (3.5) and 90% (4.5) both used to
round to quality=4, silently losing the distinction between two meaningfully
different scores.
"""
from __future__ import annotations

import asyncio
import uuid

import app.services.knowledge_model as knowledge_model

USER_ID = str(uuid.uuid4())
CONCEPT_ID = str(uuid.uuid4())


class _FakeSupabase:
    def __init__(self):
        self.reviews: list[tuple[str, int]] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return []

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        return [{**rows, "id": str(uuid.uuid4())}]

    async def update(self, table, patch, *, filters):
        return [patch]


def test_observe_grade_does_not_collapse_70_and_90_percent_to_the_same_quality(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(knowledge_model, "supabase", fake)
    seen: list[int] = []

    async def fake_review(user_id, concept_id, quality, *, confidence=None):
        seen.append(quality)
        return {}

    monkeypatch.setattr(knowledge_model, "review", fake_review)

    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 70))
    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 90))

    assert seen == [4, 5]  # previously both rounded to 4


def test_observe_grade_matches_its_own_documented_examples(monkeypatch):
    monkeypatch.setattr(knowledge_model, "supabase", _FakeSupabase())
    seen: list[int] = []

    async def fake_review(user_id, concept_id, quality, *, confidence=None):
        seen.append(quality)
        return {}

    monkeypatch.setattr(knowledge_model, "review", fake_review)

    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 100))
    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 60))

    assert seen == [5, 3]


def test_observe_grade_clamps_quality_to_0_through_5(monkeypatch):
    monkeypatch.setattr(knowledge_model, "supabase", _FakeSupabase())
    seen: list[int] = []

    async def fake_review(user_id, concept_id, quality, *, confidence=None):
        seen.append(quality)
        return {}

    monkeypatch.setattr(knowledge_model, "review", fake_review)

    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 0))
    asyncio.run(knowledge_model.observe_grade(USER_ID, [CONCEPT_ID], 120))

    assert seen == [0, 5]


def test_observe_grade_reviews_every_concept_id():
    fake = _FakeSupabase()
    calls = []

    async def fake_review(user_id, concept_id, quality, *, confidence=None):
        calls.append(concept_id)
        return {}

    import unittest.mock
    with unittest.mock.patch.object(knowledge_model, "supabase", fake), \
         unittest.mock.patch.object(knowledge_model, "review", fake_review):
        asyncio.run(knowledge_model.observe_grade(USER_ID, ["a", "b", "c"], 85))

    assert calls == ["a", "b", "c"]


# ---- compute_sm2 -----------------------------------------------------------------

def test_compute_sm2_first_successful_review_sets_interval_to_one_day():
    ease, reps, interval = knowledge_model.compute_sm2(quality=4, ease=2.5, reps=0, interval=0)
    assert reps == 1
    assert interval == 1


def test_compute_sm2_second_successful_review_sets_interval_to_six_days():
    ease, reps, interval = knowledge_model.compute_sm2(quality=4, ease=2.5, reps=1, interval=1)
    assert reps == 2
    assert interval == 6


def test_compute_sm2_failing_quality_resets_repetitions_and_interval():
    ease, reps, interval = knowledge_model.compute_sm2(quality=1, ease=2.5, reps=5, interval=30)
    assert reps == 0
    assert interval == 1


def test_compute_sm2_ease_never_drops_below_the_1_3_floor():
    ease, reps, interval = knowledge_model.compute_sm2(quality=0, ease=1.3, reps=3, interval=10)
    assert ease >= 1.3
