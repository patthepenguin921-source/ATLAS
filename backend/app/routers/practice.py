"""Past practice quizzes/tests -- browsing history of what `Tutor.quiz`
generated (see `app.services.practice`). Generating a new one still goes
through `POST /agents/tutor/quiz` (or the chat agent's `generate_practice_quiz`
tool), which persists a row here as a side effect; this router only reads
and manages that history.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.schemas import SubmitPracticeRequest
from app.services import mistake_analysis, practice as practice_service

router = APIRouter(prefix="/practice", tags=["practice"])


@router.get("")
async def list_practice(course_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    return await practice_service.list_sessions(user.id, course_id=course_id)


@router.get("/{session_id}")
async def get_practice(session_id: str, user: CurrentUser = Depends(get_current_user)):
    row = await practice_service.get_session(user.id, session_id)
    if not row:
        raise HTTPException(404, "Not found")
    return row


@router.post("/{session_id}/submit")
async def submit_practice(
    session_id: str, body: SubmitPracticeRequest, user: CurrentUser = Depends(get_current_user)
):
    """Grades a completed quiz/test against its own answer key, records any
    wrong answers as mistakes (unless the student turned that off in
    Settings) and updates the concept knowledge model, then saves the
    resulting score -- see app.services.mistake_analysis. This is the
    "smart" grading path; `PATCH /{session_id}/score` remains for a plain
    self-reported percentage on older sessions."""
    try:
        return await mistake_analysis.grade_submission(user.id, session_id, body.answers)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{session_id}/score")
async def score_practice(session_id: str, body: dict, user: CurrentUser = Depends(get_current_user)):
    """Self-reported correct/total after reviewing answers (e.g. 4/5 -> 80)
    -- entirely optional, just lets past practice show how it went."""
    score = body.get("score")
    if score is None:
        raise HTTPException(422, "Missing score")
    row = await practice_service.record_score(user.id, session_id, float(score))
    if not row:
        raise HTTPException(404, "Not found")
    return row


@router.delete("/{session_id}", status_code=204)
async def delete_practice(session_id: str, user: CurrentUser = Depends(get_current_user)):
    await practice_service.delete_session(user.id, session_id)
    return None
