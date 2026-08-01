"""Study availability -- how many minutes a student actually has to study on
a given day, since that varies wildly (practice/work some days, nothing
others). The Planner used to assume a flat 180 minutes every day; this is
what lets it read the real number instead.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.supabase_client import eq, supabase

# Used for any weekday the student hasn't configured yet -- a deliberately
# modest guess (not the old 180) so an unconfigured schedule doesn't
# overload a day that might actually have very little free time.
DEFAULT_MINUTES = 60


async def get_weekly_availability(user_id: str) -> dict[int, int]:
    """All 7 days, Monday(0)..Sunday(6) -- any day the student hasn't set
    explicitly falls back to `DEFAULT_MINUTES` rather than being omitted,
    so the frontend always has a full week to render."""
    rows = await supabase.select(
        "study_availability", columns="day_of_week,minutes",
        filters={"user_id": eq(user_id)},
    ) or []
    by_day = {r["day_of_week"]: r["minutes"] for r in rows}
    return {day: by_day.get(day, DEFAULT_MINUTES) for day in range(7)}


async def get_minutes_for(user_id: str, for_date: date) -> int:
    rows = await supabase.select(
        "study_availability", columns="minutes",
        filters={"user_id": eq(user_id), "day_of_week": eq(str(for_date.weekday()))},
        limit=1,
    ) or []
    return rows[0]["minutes"] if rows else DEFAULT_MINUTES


async def set_weekly_availability(user_id: str, schedule: dict[int, int]) -> dict[int, int]:
    """Upserts every day given in one shot (the settings form always submits
    all 7 at once) -- `minutes: 0` is a legitimate, explicit "no time that
    day" rather than treated as unset."""
    rows: list[dict[str, Any]] = [
        {"user_id": user_id, "day_of_week": day, "minutes": max(0, minutes)}
        for day, minutes in schedule.items()
        if 0 <= day <= 6
    ]
    if rows:
        await supabase.insert(
            "study_availability", rows, upsert=True, on_conflict="user_id,day_of_week",
        )
    return await get_weekly_availability(user_id)
