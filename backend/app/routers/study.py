"""Study availability -- how many minutes a student has to study each day
of the week (see app.services.schedule), read by the Planner instead of a
flat guess.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.schemas import StudyAvailabilityRequest
from app.services import schedule

router = APIRouter(prefix="/study-availability", tags=["study"])


@router.get("")
async def get_availability(user: CurrentUser = Depends(get_current_user)):
    return await schedule.get_weekly_availability(user.id)


@router.put("")
async def put_availability(
    body: StudyAvailabilityRequest, user: CurrentUser = Depends(get_current_user)
):
    return await schedule.set_weekly_availability(user.id, body.schedule)
