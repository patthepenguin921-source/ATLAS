"""`app.services.analytics.at_risk_assignments` -- the risk score behind the
dashboard/analytics "at risk" lists and the Planner's prioritization.

Regression coverage for two bugs fixed alongside the new minor(0.3)/
major(0.7) assignment weight (see `app.services.grading`):

1. `missing`/`late` assignments were excluded entirely (the query only ever
   looked at `not_started`/`in_progress` with a future due_date), hiding
   exactly the items already hurting the grade the most.
2. The "heavy category" 2x multiplier was a hardcoded guess unrelated to any
   real per-course weighting -- an explicit minor/major weight the student
   sets must now override that guess.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.supabase_client import supabase
from app.services import analytics

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())


def _row_matches(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    """Enough of PostgREST's filter-string semantics (`eq.`, `in.(...)`,
    `gte.`) to exercise `at_risk_assignments`'s real query shape -- unlike a
    filter-ignoring fake, this actually proves the `status` filter change
    (adding missing/late, dropping the old `due_date gte.now`) does what it
    claims rather than relying on the production query being correct by
    assumption."""
    for key, want in (filters or {}).items():
        value = row.get(key)
        if not isinstance(want, str):
            if value != want:
                return False
        elif want.startswith("in.(") and want.endswith(")"):
            if value not in want[len("in.("):-1].split(","):
                return False
        elif want.startswith("eq."):
            if str(value) != want[len("eq."):]:
                return False
        elif want.startswith("gte."):
            if value is None or str(value) < want[len("gte."):]:
                return False
        # unsupported operators (e.g. this test suite's not.in./lt.) aren't
        # used by at_risk_assignments, so aren't implemented here.
    return True


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [r for r in self.rows if _row_matches(r, filters)]


def _install(monkeypatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(supabase, "select", FakeSupabase(rows).select)


def _assignment(**overrides) -> dict[str, Any]:
    base = {
        "id": str(uuid.uuid4()), "user_id": USER_ID, "title": "Untitled", "course_id": COURSE_ID,
        "category": "homework", "status": "not_started", "due_date": None,
        "difficulty": None, "points_possible": None, "weight": None,
        "estimated_minutes": None,
    }
    base.update(overrides)
    return base


def test_missing_and_late_assignments_are_included_and_ranked_highest(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Missed Quiz", status="missing",
                    due_date=(now - timedelta(days=5)).isoformat(), points_possible=20),
        _assignment(title="Upcoming Homework", status="not_started",
                    due_date=(now + timedelta(days=9)).isoformat(), points_possible=20),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    titles = [r["title"] for r in result]
    assert "Missed Quiz" in titles  # previously excluded outright
    assert result[0]["title"] == "Missed Quiz"  # ranks above a distant future item
    assert result[0]["overdue"] is True


def test_explicit_major_weight_outranks_an_unweighted_heavy_category(monkeypatch):
    now = datetime.now(timezone.utc)
    due = (now + timedelta(days=3)).isoformat()
    rows = [
        # Same due date/points/difficulty -- only the weight differs.
        _assignment(title="Weighted Minor", status="not_started", due_date=due,
                    points_possible=50, difficulty=3, weight=0.3, category="homework"),
        _assignment(title="Guessed Heavy", status="in_progress", due_date=due,
                    points_possible=50, difficulty=3, weight=None, category="test"),
        _assignment(title="Weighted Major", status="not_started", due_date=due,
                    points_possible=50, difficulty=3, weight=0.7, category="homework"),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))
    by_title = {r["title"]: r for r in result}

    # An explicit major (0.7) weight scores strictly higher than the old
    # category-guess "heavy" multiplier (2x, equivalent to weight=0.3's 1x).
    assert by_title["Weighted Major"]["risk_score"] > by_title["Guessed Heavy"]["risk_score"]
    # An explicit minor (0.3) weight is equivalent to the old "not heavy" 1x.
    assert by_title["Weighted Minor"]["risk_score"] == pytest.approx(
        by_title["Guessed Heavy"]["risk_score"] / 2, rel=1e-6
    )


def test_test_and_exam_categories_are_floored_to_high_risk(monkeypatch):
    """A low-point, far-off test/exam would otherwise score "low" by the
    raw formula -- its category alone should still put it at "high"."""
    now = datetime.now(timezone.utc)
    far_off = (now + timedelta(days=9)).isoformat()
    rows = [
        _assignment(title="Small Test", category="test", status="not_started",
                    due_date=far_off, points_possible=5, difficulty=1),
        _assignment(title="Small Exam", category="exam", status="not_started",
                    due_date=far_off, points_possible=5, difficulty=1),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))
    by_title = {r["title"]: r for r in result}

    assert by_title["Small Test"]["risk_level"] == "high"
    assert by_title["Small Exam"]["risk_level"] == "high"


def test_quiz_category_is_floored_to_medium_risk(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Pop Quiz", category="quiz", status="not_started",
                    due_date=(now + timedelta(days=9)).isoformat(),
                    points_possible=5, difficulty=1),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result[0]["risk_level"] == "medium"


def test_major_weight_is_floored_to_high_risk_even_outside_test_exam_category(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Major Project", category="project", status="not_started",
                    due_date=(now + timedelta(days=9)).isoformat(),
                    points_possible=5, difficulty=1, weight=0.7),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result[0]["risk_level"] == "high"


def test_category_floor_never_downgrades_an_already_higher_score(monkeypatch):
    """The floor only ever raises the label -- an overdue test the formula
    already scores "extreme" must not be capped down to "high"."""
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Overdue Final Exam", category="exam", status="missing",
                    due_date=(now - timedelta(days=3)).isoformat(),
                    points_possible=200, difficulty=5, weight=0.7),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result[0]["risk_level"] == "extreme"


def test_graded_and_excused_assignments_are_never_at_risk(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Done", status="graded", due_date=(now - timedelta(days=1)).isoformat()),
        _assignment(title="Excused", status="excused", due_date=(now - timedelta(days=1)).isoformat()),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result == []


def test_overdue_flag_true_for_a_past_due_date_even_without_a_missing_status(monkeypatch):
    """A status-only check missed anything past its due_date that nothing's
    (yet) externally flagged missing/late -- e.g. a manually-added
    assignment nothing syncs a status update for."""
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Quietly overdue", status="not_started",
                    due_date=(now - timedelta(days=2)).isoformat()),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result[0]["overdue"] is True


def test_overdue_flag_false_for_a_future_due_date_and_ok_status(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        _assignment(title="Not due yet", status="not_started",
                    due_date=(now + timedelta(days=3)).isoformat()),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))

    assert result[0]["overdue"] is False


def test_zero_points_possible_is_not_treated_as_unset(monkeypatch):
    """`0 or 10` previously evaluated to 10 -- a legitimately 0-point,
    completion-only assignment must score as worth 0 points, not 10."""
    now = datetime.now(timezone.utc)
    due = (now + timedelta(days=3)).isoformat()
    rows = [
        _assignment(title="Completion only", status="not_started", due_date=due,
                    points_possible=0, difficulty=3),
        _assignment(title="Ten points", status="not_started", due_date=due,
                    points_possible=10, difficulty=3),
    ]
    _install(monkeypatch, rows)

    result = asyncio.run(analytics.at_risk_assignments(USER_ID))
    by_title = {r["title"]: r for r in result}

    assert by_title["Completion only"]["risk_score"] == 0.0
    assert by_title["Ten points"]["risk_score"] > 0.0
