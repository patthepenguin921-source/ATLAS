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
        "predicted_gpa": await analytics.predicted_gpa(user.id, True),
        "calendar": events,
        "announcements": announcements,
        "daily_plan": plan_rows[0] if plan_rows else None,
    }
