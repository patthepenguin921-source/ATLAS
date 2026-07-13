"""Analytics — performance snapshots, trends, GPA, and risk."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.services import analytics as svc

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/snapshot")
async def snapshot(user: CurrentUser = Depends(get_current_user)):
    return await svc.snapshot(user.id)


@router.get("/gpa")
async def gpa(user: CurrentUser = Depends(get_current_user)):
    return {
        "weighted": await svc.predicted_gpa(user.id, True),
        "unweighted": await svc.predicted_gpa(user.id, False),
    }


@router.get("/trends")
async def trends(days: int = 90, user: CurrentUser = Depends(get_current_user)):
    return await svc.grade_trend(user.id, days=days)


@router.get("/at-risk")
async def at_risk(user: CurrentUser = Depends(get_current_user)):
    return await svc.at_risk_assignments(user.id)


@router.get("/study-efficiency")
async def study_efficiency(days: int = 30, user: CurrentUser = Depends(get_current_user)):
    return await svc.study_efficiency(user.id, days=days)
