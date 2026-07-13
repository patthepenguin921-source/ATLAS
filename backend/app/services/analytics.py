"""Performance analytics — trends, GPA, risk, productivity.

Turns the factual memory into insight: predicted GPA, grade trajectory,
study efficiency, weakest concepts, and at-risk assignments.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase


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
    """Upcoming assignments most likely to hurt the grade (weight × imminence)."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=10)
    rows = await supabase.select(
        "assignments",
        columns="id,title,course_id,category,status,due_date,difficulty,points_possible,estimated_minutes",
        filters={
            "user_id": eq(user_id),
            "due_date": f"gte.{now.isoformat()}",
            "status": "in.(not_started,in_progress)",
        },
        order="due_date.asc",
        limit=50,
    ) or []
    scored = []
    for a in rows:
        due = datetime.fromisoformat(a["due_date"].replace("Z", "+00:00")) if a.get("due_date") else soon
        days_left = max(0.25, (due - now).total_seconds() / 86400.0)
        weight = float(a.get("points_possible") or 10)
        difficulty = float(a.get("difficulty") or 3)
        heavy = {"test", "exam", "project", "essay"}.intersection({a["category"]})
        risk = (weight * difficulty * (2.0 if heavy else 1.0)) / days_left
        scored.append({**a, "risk_score": round(risk, 2), "days_left": round(days_left, 1)})
    scored.sort(key=lambda x: x["risk_score"], reverse=True)
    return scored[:limit]


async def snapshot(user_id: str) -> dict[str, Any]:
    return {
        "predicted_gpa_weighted": await predicted_gpa(user_id, True),
        "predicted_gpa_unweighted": await predicted_gpa(user_id, False),
        "grade_trends": await grade_trend(user_id),
        "study_efficiency": await study_efficiency(user_id),
        "at_risk": await at_risk_assignments(user_id),
    }


async def record_metric(user_id: str, metric: str, value: float, course_id: str | None = None) -> None:
    await supabase.insert(
        "progress_metrics",
        {"user_id": user_id, "metric": metric, "value": value, "course_id": course_id},
    )
