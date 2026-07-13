"""Profile — the student's academic identity + preferences."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import GenericBody

router = APIRouter(prefix="/profile", tags=["profile"])

_WRITABLE = {"full_name", "school", "grade_level", "gpa_goal", "timezone", "preferences"}


@router.get("")
async def get_profile(user: CurrentUser = Depends(get_current_user)):
    rows = await supabase.select("profiles", filters={"id": eq(user.id)}, limit=1)
    if rows:
        return rows[0]
    created = await supabase.insert("profiles", {"id": user.id})
    return created[0] if created else {"id": user.id}


@router.patch("")
async def update_profile(body: GenericBody, user: CurrentUser = Depends(get_current_user)):
    patch = {k: v for k, v in body.data().items() if k in _WRITABLE}
    updated = await supabase.update("profiles", patch, filters={"id": eq(user.id)})
    return updated[0] if updated else await get_profile(user)
