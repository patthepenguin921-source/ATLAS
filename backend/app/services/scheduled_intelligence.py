"""Automated triggers for the daily plan, weekly review, and retention
decay -- the three intelligence features that, until now, only ever ran on
a schedule if n8n was set up and running (see `automation/README.md`'s
`daily-plan.workflow.json`, `weekly-review.workflow.json`, and
`refresh-retention.workflow.json`). Neither Vercel Cron (`vercel.json`) nor
Cloud Scheduler (`automation/cloud-scheduler-setup.sh`) ever called these,
because the underlying endpoints (`POST /agents/planner/daily-plan`,
`POST /agents/coach/weekly-review`, `POST /knowledge/refresh-retention`)
are scoped to one authenticated user via a JWT -- there's no logged-in
session for a scheduler to act as, which is exactly the gap
`app.integrations.run_sync_for_all` already solved for PowerSchool/Schoology
sync. This module is that same pattern applied to the three features that
didn't have it: iterate every user, run their update, keep going on a
per-user failure so one broken account doesn't block the rest of the sweep.

Every user is one row in `profiles` (created on first `GET /profile` call --
see `app.routers.profile`), so that table is used as the user registry.
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Any

from app.agents.coach import Coach
from app.agents.planner import Planner
from app.core.supabase_client import supabase
from app.services import knowledge_model, schedule


async def _all_user_ids() -> list[str]:
    rows = await supabase.select("profiles", columns="id", limit=5000) or []
    return [r["id"] for r in rows]


async def run_daily_plans_for_all() -> dict[str, Any]:
    today = date_cls.today()
    ok, failed = 0, 0
    for user_id in await _all_user_ids():
        try:
            minutes = await schedule.get_minutes_for(user_id, today)
            await Planner().generate_daily_plan(user_id, today.isoformat(), minutes)
            ok += 1
        except Exception:  # noqa: BLE001 -- one account's failure shouldn't stop the sweep
            failed += 1
    return {"generated": ok, "failed": failed}


async def run_weekly_reviews_for_all() -> dict[str, Any]:
    ok, failed = 0, 0
    for user_id in await _all_user_ids():
        try:
            await Coach().weekly_review(user_id)
            ok += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"generated": ok, "failed": failed}


async def run_retention_refresh_for_all() -> dict[str, Any]:
    ok, failed, total_updated = 0, 0, 0
    for user_id in await _all_user_ids():
        try:
            total_updated += await knowledge_model.refresh_retention(user_id)
            ok += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"users_processed": ok, "failed": failed, "concepts_updated": total_updated}
