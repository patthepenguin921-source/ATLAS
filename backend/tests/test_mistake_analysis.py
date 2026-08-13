"""`app.services.mistake_analysis` -- grading a submitted practice quiz,
recording wrong answers into `mistakes`, and feeding outcomes into the
concept knowledge model (SM-2 spaced repetition). Before this module
existed, nothing in the codebase ever wrote to `mistakes` or called
`knowledge_model.review` from a quiz -- this is what closes that loop."""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.services.mistake_analysis as mistake_analysis

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


def _session(**overrides):
    base = {
        "id": SESSION_ID,
        "user_id": USER_ID,
        "course_id": COURSE_ID,
        "questions": [
            {"q": "What is 2+2?", "type": "short_answer", "answer": "4",
             "explanation": "Basic addition.", "concept": "Arithmetic"},
            {"q": "Pick the noun", "type": "multiple_choice",
             "choices": ["run", "quickly", "dog", "blue"], "answer": "dog",
             "explanation": "A dog is a thing.", "concept": "Grammar"},
        ],
    }
    base.update(overrides)
    return base


class _FakeArchivist:
    """Stands in for app.agents.archivist.Archivist -- avoids touching the
    real embedding/LLM-backed concept upsert in a unit test."""

    async def _upsert_concept(self, user_id, concept):
        name = (concept or {}).get("name")
        return f"concept:{name}" if name else None


class _FakeSupabase:
    def __init__(self, preferences=None, documents=None, document_concepts=None):
        self.preferences = preferences if preferences is not None else {}
        self.documents = documents or []
        self.document_concepts = document_concepts or []
        self.inserts: list[dict] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        filters = filters or {}
        if table == "profiles":
            return [{"id": USER_ID, "preferences": self.preferences}]
        if table == "documents":
            aid = filters.get("assignment_id", "").removeprefix("eq.")
            return [d for d in self.documents if d.get("assignment_id") == aid]
        if table == "document_concepts":
            raw = filters.get("document_id", "")
            ids = raw[len("in.("):-1].split(",") if raw.startswith("in.") else [raw.removeprefix("eq.")]
            return [c for c in self.document_concepts if c["document_id"] in ids]
        return []

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        self.inserts.append({"table": table, "rows": rows})
        return [rows]


class _FakeSettings:
    def __init__(self, has_llm: bool):
        self.has_llm = has_llm


def _install(
    monkeypatch, *, session=None, preferences=None, llm_results=None, score_calls=None,
    has_llm=True, documents=None, document_concepts=None,
):
    fake_db = _FakeSupabase(preferences=preferences, documents=documents, document_concepts=document_concepts)
    monkeypatch.setattr(mistake_analysis, "supabase", fake_db)
    monkeypatch.setattr(mistake_analysis, "Archivist", _FakeArchivist)
    monkeypatch.setattr(mistake_analysis, "settings", _FakeSettings(has_llm))

    async def fake_get_session(user_id, session_id):
        assert user_id == USER_ID
        return session

    scored: list[tuple] = score_calls if score_calls is not None else []

    async def fake_record_score(user_id, session_id, score):
        scored.append((session_id, score))

    monkeypatch.setattr(mistake_analysis.practice, "get_session", fake_get_session)
    monkeypatch.setattr(mistake_analysis.practice, "record_score", fake_record_score)

    async def fake_llm_grade(questions, answers):
        return llm_results

    monkeypatch.setattr(mistake_analysis, "_llm_grade", fake_llm_grade)

    reviews: list[tuple] = []

    async def fake_review(user_id, concept_id, quality):
        reviews.append((concept_id, quality))

    monkeypatch.setattr(mistake_analysis.knowledge_model, "review", fake_review)

    return fake_db, scored, reviews


# ---- heuristic grading (no LLM) -------------------------------------------------

def test_heuristic_grade_matches_exact_short_answer():
    q = {"type": "short_answer", "answer": "Photosynthesis"}
    assert mistake_analysis._heuristic_grade(q, "  photosynthesis ") is True
    assert mistake_analysis._heuristic_grade(q, "something else") is False


def test_heuristic_grade_matches_multiple_choice_letter():
    q = {"type": "multiple_choice", "choices": ["run", "quickly", "dog", "blue"], "answer": "dog"}
    assert mistake_analysis._heuristic_grade(q, "c") is True
    assert mistake_analysis._heuristic_grade(q, "a") is False


def test_heuristic_grade_treats_blank_as_incorrect():
    q = {"type": "short_answer", "answer": "4"}
    assert mistake_analysis._heuristic_grade(q, None) is False
    assert mistake_analysis._heuristic_grade(q, "") is False


# ---- grade_submission, LLM path ---------------------------------------------------

def test_grade_submission_uses_llm_grading_and_records_mistake(monkeypatch):
    llm_results = [
        {"i": 0, "correct": True, "mistake_type": None, "correction": None},
        {"i": 1, "correct": False, "mistake_type": "careless", "correction": "It's 'dog', not 'run'."},
    ]
    fake_db, scored, reviews = _install(monkeypatch, session=_session(), llm_results=llm_results)

    result = asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, ["4", "run"]))

    assert result["score"] == 50.0
    assert result["results"][0]["correct"] is True
    assert result["results"][1]["correct"] is False
    assert result["results"][1]["mistake_type"] == "careless"

    mistake_inserts = [i for i in fake_db.inserts if i["table"] == "mistakes"]
    assert len(mistake_inserts) == 1
    assert mistake_inserts[0]["rows"]["description"] == "Pick the noun"
    assert mistake_inserts[0]["rows"]["mistake_type"] == "careless"
    assert mistake_inserts[0]["rows"]["concept_id"] == "concept:Grammar"

    assert scored == [(SESSION_ID, 50.0)]


def test_grade_submission_reviews_every_concept_with_quality_based_on_correctness(monkeypatch):
    llm_results = [
        {"i": 0, "correct": True, "mistake_type": None, "correction": None},
        {"i": 1, "correct": False, "mistake_type": "conceptual", "correction": "Wrong."},
    ]
    fake_db, scored, reviews = _install(monkeypatch, session=_session(), llm_results=llm_results)

    asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, ["4", "run"]))

    assert ("concept:Arithmetic", 5) in reviews  # correct answer -> perfect recall
    assert ("concept:Grammar", 1) in reviews     # conceptual miss -> reset hard


# ---- grade_submission, heuristic fallback (no LLM configured) --------------------

def test_grade_submission_falls_back_to_heuristic_when_llm_unavailable(monkeypatch):
    fake_db, scored, reviews = _install(monkeypatch, session=_session(), llm_results=None, has_llm=False)

    result = asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, ["4", "c"]))

    assert result["score"] == 100.0
    assert all(r["correct"] for r in result["results"])
    assert not [i for i in fake_db.inserts if i["table"] == "mistakes"]


# ---- preferences ------------------------------------------------------------------

def test_grade_submission_skips_mistake_recording_when_tracking_disabled(monkeypatch):
    llm_results = [
        {"i": 0, "correct": False, "mistake_type": "knowledge_gap", "correction": "Nope."},
        {"i": 1, "correct": False, "mistake_type": "knowledge_gap", "correction": "Nope."},
    ]
    fake_db, scored, reviews = _install(
        monkeypatch, session=_session(), preferences={"mistake_tracking_enabled": False}, llm_results=llm_results,
    )

    asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, [None, None]))

    assert not [i for i in fake_db.inserts if i["table"] == "mistakes"]
    # The knowledge model still updates even when mistake *recording* is off --
    # the two are independent toggles-worth of behavior sharing one preference.
    assert len(reviews) == 2


# ---- errors -------------------------------------------------------------------------

def test_grade_submission_raises_lookup_error_when_session_missing(monkeypatch):
    _install(monkeypatch, session=None)
    with pytest.raises(LookupError):
        asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, []))


def test_grade_submission_raises_value_error_when_session_has_no_questions(monkeypatch):
    _install(monkeypatch, session=_session(questions=[]))
    with pytest.raises(ValueError):
        asyncio.run(mistake_analysis.grade_submission(USER_ID, SESSION_ID, []))


# ---- record_from_synced_grade --------------------------------------------------------

ASSIGNMENT_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())
CONCEPT_ID = str(uuid.uuid4())


def _with_linked_concept(monkeypatch, **install_kwargs):
    fake_db, _, _ = _install(
        monkeypatch,
        documents=[{"id": DOC_ID, "assignment_id": ASSIGNMENT_ID}],
        document_concepts=[{"document_id": DOC_ID, "concept_id": CONCEPT_ID}],
        **install_kwargs,
    )
    return fake_db


def test_synced_grade_updates_knowledge_model_even_above_mistake_threshold(monkeypatch):
    fake_db = _with_linked_concept(monkeypatch)
    observed = []

    async def fake_observe_grade(user_id, concept_ids, percentage):
        observed.append((concept_ids, percentage))

    monkeypatch.setattr(mistake_analysis.knowledge_model, "observe_grade", fake_observe_grade)

    asyncio.run(mistake_analysis.record_from_synced_grade(
        USER_ID, assignment_id=ASSIGNMENT_ID, course_id=COURSE_ID, title="Quiz 4",
        score=95, points_possible=100, percentage=None,
    ))

    assert observed == [([CONCEPT_ID], 95.0)]
    assert not [i for i in fake_db.inserts if i["table"] == "mistakes"]


def test_synced_grade_below_threshold_records_a_mistake(monkeypatch):
    fake_db = _with_linked_concept(monkeypatch)

    async def fake_observe_grade(user_id, concept_ids, percentage):
        pass

    monkeypatch.setattr(mistake_analysis.knowledge_model, "observe_grade", fake_observe_grade)

    asyncio.run(mistake_analysis.record_from_synced_grade(
        USER_ID, assignment_id=ASSIGNMENT_ID, course_id=COURSE_ID, title="Quiz 4",
        score=40, points_possible=100, percentage=None,
    ))

    mistake_inserts = [i for i in fake_db.inserts if i["table"] == "mistakes"]
    assert len(mistake_inserts) == 1
    assert mistake_inserts[0]["rows"]["concept_id"] == CONCEPT_ID
    assert mistake_inserts[0]["rows"]["mistake_type"] == "knowledge_gap"
    assert 'Quiz 4' in mistake_inserts[0]["rows"]["description"]


def test_synced_grade_respects_mistake_tracking_disabled(monkeypatch):
    fake_db = _with_linked_concept(monkeypatch, preferences={"mistake_tracking_enabled": False})

    async def fake_observe_grade(user_id, concept_ids, percentage):
        pass

    monkeypatch.setattr(mistake_analysis.knowledge_model, "observe_grade", fake_observe_grade)

    asyncio.run(mistake_analysis.record_from_synced_grade(
        USER_ID, assignment_id=ASSIGNMENT_ID, course_id=COURSE_ID, title="Quiz 4",
        score=40, points_possible=100, percentage=None,
    ))

    assert not [i for i in fake_db.inserts if i["table"] == "mistakes"]


def test_synced_grade_with_no_linked_documents_does_nothing(monkeypatch):
    fake_db, _, _ = _install(monkeypatch, documents=[], document_concepts=[])
    called = []

    async def fake_observe_grade(user_id, concept_ids, percentage):
        called.append(True)

    monkeypatch.setattr(mistake_analysis.knowledge_model, "observe_grade", fake_observe_grade)

    asyncio.run(mistake_analysis.record_from_synced_grade(
        USER_ID, assignment_id=ASSIGNMENT_ID, course_id=COURSE_ID, title="Quiz 4",
        score=40, points_possible=100, percentage=None,
    ))

    assert not called
    assert not fake_db.inserts


def test_synced_grade_computes_percentage_from_score_and_points(monkeypatch):
    _with_linked_concept(monkeypatch)
    observed = []

    async def fake_observe_grade(user_id, concept_ids, percentage):
        observed.append(percentage)

    monkeypatch.setattr(mistake_analysis.knowledge_model, "observe_grade", fake_observe_grade)

    asyncio.run(mistake_analysis.record_from_synced_grade(
        USER_ID, assignment_id=ASSIGNMENT_ID, course_id=COURSE_ID, title="Quiz 4",
        score=18, points_possible=20, percentage=None,
    ))

    assert observed == [90.0]
