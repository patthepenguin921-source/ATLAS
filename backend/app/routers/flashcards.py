"""Flashcards -- generated from a document or a whole unit's documents (see
app.services.flashcards) and reviewed with the same spaced-repetition math
as the concept knowledge model (app.services.knowledge_model.compute_sm2).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import FlashcardGenerateRequest, FlashcardReviewRequest
from app.services import flashcards as flashcards_service

router = APIRouter(prefix="/flashcards", tags=["flashcards"])


@router.post("/generate")
async def generate(body: FlashcardGenerateRequest, user: CurrentUser = Depends(get_current_user)):
    if not body.document_id and not body.folder_id and not body.course_id:
        raise HTTPException(400, "Pick a document, a unit, or a class to generate flashcards from.")
    result = await flashcards_service.generate(
        user.id, document_id=body.document_id, course_id=body.course_id,
        folder_id=body.folder_id, max_cards=body.max_cards,
    )
    if result.get("status") == "error":
        raise HTTPException(422, result["message"])
    return result


@router.get("")
async def list_flashcards(
    course_id: str | None = None,
    folder_id: str | None = None,
    document_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    filters: dict[str, str] = {"user_id": eq(user.id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    if folder_id:
        filters["folder_id"] = eq(folder_id)
    if document_id:
        filters["document_id"] = eq(document_id)
    return await supabase.select(
        "flashcards", filters=filters, order="created_at.desc", limit=500,
    )


@router.get("/review-queue")
async def review_queue(user: CurrentUser = Depends(get_current_user)):
    """Cards due for spaced-repetition review right now."""
    now = datetime.now(timezone.utc).isoformat()
    return await supabase.select(
        "flashcards", filters={"user_id": eq(user.id), "next_review_at": f"lte.{now}"},
        order="next_review_at.asc", limit=100,
    )


@router.post("/{flashcard_id}/review")
async def review(
    flashcard_id: str, body: FlashcardReviewRequest, user: CurrentUser = Depends(get_current_user)
):
    result = await flashcards_service.review(user.id, flashcard_id, body.quality)
    if result.get("status") == "error":
        raise HTTPException(404, result["message"])
    return result["flashcard"]


@router.delete("/{flashcard_id}", status_code=204)
async def delete_flashcard(flashcard_id: str, user: CurrentUser = Depends(get_current_user)):
    await supabase.delete("flashcards", filters={"user_id": eq(user.id), "id": eq(flashcard_id)})
    return None
