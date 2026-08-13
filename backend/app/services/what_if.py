"""Grade what-if calculator -- "what do I need on the final to get a B+."

Recomputes a course's weighted grade percentage/letter under a hypothetical
scenario (an existing grade's score changed, or one new assignment added),
using the exact same weighted-average formula as
`recompute_course_grade()` (migration 0023_assignment_weight_grade_rollup.sql)
so a projection here matches what the real rollup would show once that
grade is actually posted. Entirely read-only -- nothing computed here is
ever written back; it only mirrors the SQL function's math in Python so a
hypothetical scenario can be evaluated without faking a database row.
"""
from __future__ import annotations

from typing import Any

from app.core.supabase_client import eq, supabase

# Mirrors recompute_course_grade's letter bands exactly (migration 0023).
_LETTER_BANDS = (
    (93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"),
    (77, "C+"), (73, "C"), (70, "C-"), (67, "D+"), (65, "D"),
)


def _letter_for(pct: float | None) -> str | None:
    if pct is None:
        return None
    for threshold, letter in _LETTER_BANDS:
        if pct >= threshold:
            return letter
    return "F"


async def simulate(
    user_id: str, course_id: str, *,
    override_assignment_id: str | None = None,
    override_percentage: float | None = None,
    hypothetical_percentage: float | None = None,
    hypothetical_weight: float | None = None,
) -> dict[str, Any]:
    """Pass at most one scenario: either `override_assignment_id` +
    `override_percentage` (pretend a specific already-graded assignment
    scored differently) or `hypothetical_percentage` (+ optional
    `hypothetical_weight`, default 1 -- pretend one new, ungraded
    assignment came in at this score). Neither is required -- with
    neither set this just reports the course's real current numbers,
    recomputed the same way, as a sanity check that the math agrees."""
    course_rows = await supabase.select(
        "courses", columns="id,name,current_grade,current_letter",
        filters={"user_id": eq(user_id), "id": eq(course_id)}, limit=1,
    )
    if not course_rows:
        raise LookupError("Course not found.")
    course = course_rows[0]

    grades = await supabase.select(
        "grades", columns="id,assignment_id,percentage,weight",
        filters={"user_id": eq(user_id), "course_id": eq(course_id)}, limit=500,
    ) or []

    assignment_ids = [g["assignment_id"] for g in grades if g.get("assignment_id")]
    assignment_weights: dict[str, float] = {}
    if assignment_ids:
        rows = await supabase.select(
            "assignments", columns="id,weight",
            filters={"id": f"in.({','.join(assignment_ids)})"}, limit=500,
        ) or []
        assignment_weights = {r["id"]: r["weight"] for r in rows if r.get("weight") is not None}

    weighted_sum = 0.0
    weight_total = 0.0
    for g in grades:
        pct = g.get("percentage")
        if pct is None:
            continue
        if (
            override_assignment_id and override_percentage is not None
            and g.get("assignment_id") == override_assignment_id
        ):
            pct = override_percentage
        # Same precedence as the SQL rollup: grades.weight wins when set,
        # falling back to the assignment's own weight, then 1.
        weight = g.get("weight")
        if weight is None:
            weight = assignment_weights.get(g.get("assignment_id"), 1.0)
        weighted_sum += pct * weight
        weight_total += weight

    if hypothetical_percentage is not None:
        weight = hypothetical_weight if hypothetical_weight is not None else 1.0
        weighted_sum += hypothetical_percentage * weight
        weight_total += weight

    projected = round(weighted_sum / weight_total, 3) if weight_total else None
    return {
        "course_id": course_id, "course_name": course.get("name"),
        "current_grade": course.get("current_grade"), "current_letter": course.get("current_letter"),
        "projected_grade": projected, "projected_letter": _letter_for(projected),
    }
