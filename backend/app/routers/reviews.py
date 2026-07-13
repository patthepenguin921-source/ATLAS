"""Weekly reviews & daily plans — read access to generated artifacts."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/weekly")
async def weekly(user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "weekly_reviews", filters={"user_id": eq(user.id)},
        order="week_start.desc", limit=20,
    )


@router.get("/plans")
async def plans(user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "daily_plans", filters={"user_id": eq(user.id)},
        order="plan_date.desc", limit=30,
    )
