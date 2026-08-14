"""Performance analytics — trends, GPA, risk, productivity.

Turns the factual memory into insight: predicted GPA, grade trajectory,
study efficiency, weakest concepts, and at-risk assignments.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase
from app.services.grading import ASSIGNMENT_WEIGHTS

# Fallback importance guess for an assignment with no explicit minor/major
# weight set (see ASSIGNMENT_WEIGHTS) -- once a real weight exists it always
# wins over this category-based guess.
_HEAVY_CATEGORIES = {"test", "exam", "project", "essay"}

# Category-based *floors* on the final risk_level label -- a major-weighted
# assignment (or a test/exam, even one with no explicit weight set) never
# reads as less than "high" risk, and a quiz never less than "medium",
# regardless of what the raw points x difficulty x urgency formula happens
# to compute (a low-point test still deserves more attention than a
# high-point daily homework assignment). This only ever raises the label the
# formula produced, never lowers it -- an overdue major test that already
# scores "extreme" stays "extreme", it doesn't get capped down to "high".
_HIGH_RISK_FLOOR_CATEGORIES = {"test", "exam"}
_MEDIUM_RISK_FLOOR_CATEGORIES = {"quiz"}
_RISK_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "extreme": 3}


async def predicted_gpa(user_id: str, weighted: bool = True) -> float | None:
    val = await supabase.rpc("predicted_gpa", {"p_user_id": user_id, "p_weighted": weighted})
    return val


async def grade_trend(user_id: str, *, days: int = 90) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    grades = await supabase.select(
        "grades",
        columns="course_id,percentage,graded_at",
        filters={"user_id": eq(user_id), "graded_at": f"gte.{since}", "percentage": "not.is.null"},
        order="graded_at.asc",
    ) or []
    by_course: dict[str, list[float]] = defaultdict(list)
    for g in grades:
        by_course[g["course_id"]].append(float(g["percentage"]))
    out = []
    for cid, vals in by_course.items():
        if len(vals) >= 2:
            direction = "up" if vals[-1] > vals[0] else ("down" if vals[-1] < vals[0] else "flat")
            out.append({
                "course_id": cid, "first": vals[0], "latest": vals[-1],
                "average": round(sum(vals) / len(vals), 2), "samples": len(vals),
                "direction": direction, "delta": round(vals[-1] - vals[0], 2),
            })
    return out


async def study_efficiency(user_id: str, *, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sessions = await supabase.select(
        "study_sessions",
        columns="duration_minutes,focus_rating,started_at,technique",
        filters={"user_id": eq(user_id), "started_at": f"gte.{since}"},
    ) or []
    total = sum((s.get("duration_minutes") or 0) for s in sessions)
    focus = [s["focus_rating"] for s in sessions if s.get("focus_rating")]
    by_hour: dict[int, list[int]] = defaultdict(list)
    for s in sessions:
        if s.get("started_at") and s.get("focus_rating"):
            hour = datetime.fromisoformat(s["started_at"].replace("Z", "+00:00")).hour
            by_hour[hour].append(s["focus_rating"])
    best_hour = None
    if by_hour:
        best_hour = max(by_hour, key=lambda h: sum(by_hour[h]) / len(by_hour[h]))
    return {
        "sessions": len(sessions),
        "total_minutes": total,
        "avg_focus": round(sum(focus) / len(focus), 2) if focus else None,
        "most_productive_hour": best_hour,
    }


async def at_risk_assignments(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Assignments most likely to hurt the grade: points x difficulty x
    importance, divided by how much time is left to act.

    Importance comes from the assignment's own minor(0.3)/major(0.7) weight
    when the student set one (see `app.agents.tools`' add_assignment tool
    and the manual Add Assignment form), normalized against the minor
    weight so 0.3 -> 1x and 0.7 -> ~2.3x -- otherwise it falls back to a
    guess from the category alone, same as before.

    Already missing/late assignments are included (previously excluded
    entirely by a `status in (not_started, in_progress)` filter, which hid
    exactly the items already hurting the grade the most) and, along with
    anything else whose due_date has passed, always get the maximum
    urgency term via the same `days_left` floor used for a due-in-6-hours
    item, rather than dividing by a negative or wildly small number.
    """
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=10)
    rows = await supabase.select(
        "assignments",
        columns="id,title,course_id,category,status,due_date,difficulty,"
                 "points_possible,weight,estimated_minutes,risk_override",
        filters={
            "user_id": eq(user_id),
            "status": "in.(not_started,in_progress,missing,late)",
        },
        order="due_date.asc",
        limit=50,
    ) or []
    scored = []
    for a in rows:
        due = datetime.fromisoformat(a["due_date"].replace("Z", "+00:00")) if a.get("due_date") else soon
        days_left = max(0.25, (due - now).total_seconds() / 86400.0)
        # `or` would treat a legitimately-0-point/0-difficulty assignment
        # (e.g. a completion-only item synced with points_possible=0) the
        # same as an unset one -- explicit None checks, same pattern
        # `weight` below already uses.
        points = float(a["points_possible"]) if a.get("points_possible") is not None else 10.0
        difficulty = float(a["difficulty"]) if a.get("difficulty") is not None else 3.0
        if a.get("weight") is not None:
            importance = float(a["weight"]) / ASSIGNMENT_WEIGHTS["minor"]
        else:
            importance = 2.0 if a["category"] in _HEAVY_CATEGORIES else 1.0
        risk = (points * difficulty * importance) / days_left
        scored.append({
            **a,
            "risk_score": round(risk, 2),
            "risk_level": _risk_level(risk, a["category"], a.get("weight"), a.get("risk_override")),
            "days_left": round(days_left, 1),
            # A status-based flag alone missed anything whose due_date has
            # simply passed but hasn't (yet) been externally marked
            # missing/late -- e.g. a manually-added assignment nothing ever
            # syncs a status update for.
            "overdue": a["status"] in ("missing", "late") or (bool(a.get("due_date")) and due < now),
        })
    scored.sort(key=lambda x: x["risk_score"], reverse=True)
    return scored[:limit]


def _risk_level(
    score: float, category: str | None = None, weight: float | None = None,
    override: str | None = None,
) -> str:
    """Bucket a raw risk score into a human label (low/medium/high/extreme),
    then apply this category's floor (see `_HIGH_RISK_FLOOR_CATEGORIES`/
    `_MEDIUM_RISK_FLOOR_CATEGORIES`) if it's stricter than what the score
    alone produced -- unless the student has manually set `risk_override`
    (see assignments.risk_override, migration 0029), which always wins
    outright over both the formula and the category floor."""
    if override in _RISK_LEVEL_ORDER:
        return override
    if score >= 120:
        level = "extreme"
    elif score >= 60:
        level = "high"
    elif score >= 25:
        level = "medium"
    else:
        level = "low"

    floor = None
    if (weight is not None and weight >= ASSIGNMENT_WEIGHTS["major"]) or category in _HIGH_RISK_FLOOR_CATEGORIES:
        floor = "high"
    elif category in _MEDIUM_RISK_FLOOR_CATEGORIES:
        floor = "medium"
    if floor and _RISK_LEVEL_ORDER[floor] > _RISK_LEVEL_ORDER[level]:
        return floor
    return level


async def mistake_patterns(user_id: str, *, days: int = 90) -> list[dict[str, Any]]:
    """Groups logged mistakes -- manually recorded on an assignment, or
    auto-logged by `app.services.mistake_analysis` from a synced grade or a
    graded practice quiz -- by course + mistake_type, so "conceptual slips
    keep happening in Bio" actually surfaces as a pattern instead of staying
    a pile of individual rows nobody re-reads."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await supabase.select(
        "mistakes",
        columns="course_id,mistake_type,resolved,occurred_at",
        filters={"user_id": eq(user_id), "occurred_at": f"gte.{since}"},
        order="occurred_at.desc",
    ) or []
    by_key: dict[tuple[str | None, str], dict[str, Any]] = {}
    for m in rows:
        key = (m.get("course_id"), m.get("mistake_type") or "unspecified")
        bucket = by_key.setdefault(key, {
            "course_id": key[0], "mistake_type": key[1],
            "count": 0, "unresolved": 0, "latest": m["occurred_at"],
        })
        bucket["count"] += 1
        if not m.get("resolved"):
            bucket["unresolved"] += 1
    out = list(by_key.values())
    out.sort(key=lambda b: b["count"], reverse=True)
    return out


async def snapshot(user_id: str) -> dict[str, Any]:
    return {
        "predicted_gpa_weighted": await predicted_gpa(user_id, True),
        "predicted_gpa_unweighted": await predicted_gpa(user_id, False),
        "grade_trends": await grade_trend(user_id),
        "study_efficiency": await study_efficiency(user_id),
        "at_risk": await at_risk_assignments(user_id),
        "mistake_patterns": await mistake_patterns(user_id),
    }


async def record_metric(user_id: str, metric: str, value: float, course_id: str | None = None) -> None:
    await supabase.insert(
        "progress_metrics",
        {"user_id": user_id, "metric": metric, "value": value, "course_id": course_id},
    )
