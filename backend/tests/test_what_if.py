"""`app.services.what_if.simulate` -- the grade what-if calculator.
Mirrors `recompute_course_grade()`'s weighted-average SQL formula (migration
0023_assignment_weight_grade_rollup.sql) exactly, so a projection here
matches what the real rollup would show once a grade is actually posted."""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.services.what_if as what_if

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())


class _FakeSupabase:
    def __init__(self, course=None, grades=None, assignments=None):
        self.course = course
        self.grades = grades or []
        self.assignments = assignments or []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        if table == "courses":
            return [self.course] if self.course else []
        if table == "grades":
            return self.grades
        if table == "assignments":
            raw = (filters or {}).get("id", "")
            ids = raw[len("in.("):-1].split(",") if raw.startswith("in.") else []
            return [a for a in self.assignments if a["id"] in ids]
        return []


def _install(monkeypatch, *, course=None, grades=None, assignments=None):
    course = course or {"id": COURSE_ID, "name": "AP Biology", "current_grade": 88.0, "current_letter": "B+"}
    fake = _FakeSupabase(course=course, grades=grades, assignments=assignments)
    monkeypatch.setattr(what_if, "supabase", fake)
    return fake


def test_with_no_scenario_reflects_the_real_grades_unweighted(monkeypatch):
    grades = [
        {"id": "g1", "assignment_id": "a1", "percentage": 90.0, "weight": None},
        {"id": "g2", "assignment_id": "a2", "percentage": 80.0, "weight": None},
    ]
    _install(monkeypatch, grades=grades, assignments=[])

    result = asyncio.run(what_if.simulate(USER_ID, COURSE_ID))

    assert result["projected_grade"] == 85.0
    assert result["projected_letter"] == "B"


def test_uses_assignment_weight_when_grade_weight_is_unset(monkeypatch):
    grades = [
        {"id": "g1", "assignment_id": "a1", "percentage": 100.0, "weight": None},  # major, 0.7
        {"id": "g2", "assignment_id": "a2", "percentage": 60.0, "weight": None},   # minor, 0.3
    ]
    assignments = [{"id": "a1", "weight": 0.7}, {"id": "a2", "weight": 0.3}]
    _install(monkeypatch, grades=grades, assignments=assignments)

    result = asyncio.run(what_if.simulate(USER_ID, COURSE_ID))

    # (100*0.7 + 60*0.3) / (0.7+0.3) = 88
    assert result["projected_grade"] == 88.0


def test_grade_weight_wins_over_assignment_weight(monkeypatch):
    grades = [{"id": "g1", "assignment_id": "a1", "percentage": 50.0, "weight": 2.0}]
    assignments = [{"id": "a1", "weight": 0.3}]
    _install(monkeypatch, grades=grades, assignments=assignments)

    result = asyncio.run(what_if.simulate(USER_ID, COURSE_ID))

    assert result["projected_grade"] == 50.0  # only grade in the average either way


def test_override_scenario_swaps_one_assignments_score(monkeypatch):
    grades = [
        {"id": "g1", "assignment_id": "a1", "percentage": 70.0, "weight": None},
        {"id": "g2", "assignment_id": "a2", "percentage": 90.0, "weight": None},
    ]
    _install(monkeypatch, grades=grades, assignments=[])

    result = asyncio.run(what_if.simulate(
        USER_ID, COURSE_ID, override_assignment_id="a1", override_percentage=100.0,
    ))

    assert result["projected_grade"] == 95.0  # (100+90)/2
    assert result["current_grade"] == 88.0    # unaffected -- this is a projection, not a write


def test_hypothetical_scenario_adds_a_new_assignment_at_default_weight_one(monkeypatch):
    grades = [{"id": "g1", "assignment_id": "a1", "percentage": 90.0, "weight": None}]
    _install(monkeypatch, grades=grades, assignments=[])

    result = asyncio.run(what_if.simulate(USER_ID, COURSE_ID, hypothetical_percentage=70.0))

    assert result["projected_grade"] == 80.0  # (90+70)/2


def test_hypothetical_scenario_respects_a_custom_weight(monkeypatch):
    grades = [{"id": "g1", "assignment_id": "a1", "percentage": 90.0, "weight": 1.0}]
    _install(monkeypatch, grades=grades, assignments=[])

    result = asyncio.run(what_if.simulate(
        USER_ID, COURSE_ID, hypothetical_percentage=50.0, hypothetical_weight=3.0,
    ))

    # (90*1 + 50*3) / (1+3) = 60
    assert result["projected_grade"] == 60.0


def test_letter_bands_match_the_sql_rollup_exactly(monkeypatch):
    _install(monkeypatch, grades=[{"id": "g1", "assignment_id": "a1", "percentage": 93.0, "weight": None}])
    assert asyncio.run(what_if.simulate(USER_ID, COURSE_ID))["projected_letter"] == "A"

    _install(monkeypatch, grades=[{"id": "g1", "assignment_id": "a1", "percentage": 92.9, "weight": None}])
    assert asyncio.run(what_if.simulate(USER_ID, COURSE_ID))["projected_letter"] == "A-"

    _install(monkeypatch, grades=[{"id": "g1", "assignment_id": "a1", "percentage": 64.9, "weight": None}])
    assert asyncio.run(what_if.simulate(USER_ID, COURSE_ID))["projected_letter"] == "F"


def test_no_grades_at_all_projects_none(monkeypatch):
    _install(monkeypatch, grades=[])

    result = asyncio.run(what_if.simulate(USER_ID, COURSE_ID))

    assert result["projected_grade"] is None
    assert result["projected_letter"] is None


def test_raises_lookup_error_for_missing_course(monkeypatch):
    monkeypatch.setattr(what_if, "supabase", _FakeSupabase(course=None))
    with pytest.raises(LookupError):
        asyncio.run(what_if.simulate(USER_ID, COURSE_ID))
