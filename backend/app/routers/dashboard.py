"""Daily dashboard — the morning briefing Atlas generates each day."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.services import analytics, memory

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(user: CurrentUser = Depends(get_current_user)):
    """Everything needed for the morning briefing, in one call."""
    ctx = await memory.build_context(user.id, None, include_semantic=False)
    today = date.today().isoformat()

    plan_rows = await supabase.select(
        "daily_plans", filters={"user_id": eq(user.id), "plan_date": eq(today)}, limit=1
    )
    events = await supabase.select(
        "calendar_events", columns="title,starts_at,ends_at,kind,course_id",
        filters={"user_id": eq(user.id), "starts_at": f"gte.{today}T00:00:00Z"},
        order="starts_at.asc", limit=20,
    )
    announcements = await supabase.select(
        "announcements", columns="title,body,posted_at,course_id",
        filters={"user_id": eq(user.id)}, order="posted_at.desc.nullslast", limit=5,
    )

    return {
        "date": today,
        "courses": ctx["courses"],
        "priorities_today": ctx["upcoming"][:8],
        "overdue": ctx["overdue"],
        "review_due": ctx["review_due"],
        "estimated_workload_minutes": sum(
            (a.get("estimated_minutes") or 30) for a in ctx["upcoming"][:8]
        ),
        "at_risk": await analytics.at_risk_assignments(user.id, limit=5),
        "predicted_gpa_unweighted": await analytics.predicted_gpa(user.id, False),
        "predicted_gpa_weighted": await analytics.predicted_gpa(user.id, True),
        "calendar": events,
        "announcements": announcements,
        "daily_plan": plan_rows[0] if plan_rows else None,
    }


@router.get("/calendar-range")
async def calendar_range(
    start: str, end: str, user: CurrentUser = Depends(get_current_user),
):
    """Every class-schedule day, other calendar event, and assignment due
    date in [start, end) across every course -- the data behind the
    traditional month/week calendar view (`/calendar` in the frontend).
    `start`/`end` are plain YYYY-MM-DD dates; `end` is exclusive so a caller
    can pass the first day of the *next* period without double-counting it.

    Kept separate from the main `/dashboard` call above: that one only ever
    looks from today forward (the daily briefing), while a calendar view
    needs an arbitrary range as the student navigates month to month."""
    events = await supabase.select(
        "calendar_events",
        columns="id,title,description,starts_at,ends_at,all_day,kind,course_id",
        filters={"user_id": eq(user.id), "starts_at": f"gte.{start}",
                 "and": f"(starts_at.lt.{end})"},
        order="starts_at.asc", limit=1000,
    ) or []
    assignments = await supabase.select(
        "assignments",
        columns="id,title,category,due_date,course_id,status",
        filters={"user_id": eq(user.id), "due_date": f"gte.{start}",
                 "and": f"(due_date.lt.{end})"},
        order="due_date.asc", limit=1000,
    ) or []
    return {"events": events, "assignments": assignments}
