"""Flashcard generation -- pulls key terms + definitions out of a single
document or a whole unit's worth of documents ("gather key terms from this
doc/unit") instead of the student re-reading everything to make cards by
hand. Reviewed with the same SM-2 spaced-repetition math as the concept
knowledge model (see `app.services.knowledge_model.compute_sm2`), just
scoped to one card instead of one concept -- a generated term doesn't
necessarily correspond to an existing `concepts` row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase
from app.llm import claude
from app.services import knowledge_model

# How much raw document text to feed the extraction prompt -- generous
# enough to cover a real study guide/slideshow, capped so a big unit's worth
# of documents still fits comfortably in one call instead of failing or
# costing a fortune.
_MAX_CHARS_PER_DOC = 6000
_MAX_TOTAL_CHARS = 18000
_MAX_CARDS = 30


async def _gather_source_text(
    user_id: str, *, document_id: str | None, course_id: str | None, folder_id: str | None,
) -> tuple[str, str]:
    """Returns (source_label, concatenated text). Scope is exactly one
    document, or every ingested document in a folder/unit (optionally
    narrowed further by course)."""
    if document_id:
        doc_rows = await supabase.select(
            "documents", columns="id,title",
            filters={"user_id": eq(user_id), "id": eq(document_id)}, limit=1,
        ) or []
        if not doc_rows:
            return "", ""
        doc_ids = [document_id]
        label = doc_rows[0]["title"]
    elif folder_id or course_id:
        filters: dict[str, Any] = {"user_id": eq(user_id), "ingested": eq("true")}
        if folder_id:
            filters["folder_id"] = eq(folder_id)
        if course_id:
            filters["course_id"] = eq(course_id)
        doc_rows = await supabase.select(
            "documents", columns="id,title", filters=filters, limit=50,
        ) or []
        doc_ids = [d["id"] for d in doc_rows]
        if folder_id:
            folder = await supabase.select(
                "folders", columns="name", filters={"user_id": eq(user_id), "id": eq(folder_id)}, limit=1,
            )
            label = folder[0]["name"] if folder else "this unit"
        else:
            course = await supabase.select(
                "courses", columns="name", filters={"user_id": eq(user_id), "id": eq(course_id)}, limit=1,
            )
            label = course[0]["name"] if course else "this class"
    else:
        return "", ""

    if not doc_ids:
        return label, ""

    parts: list[str] = []
    total = 0
    for did in doc_ids:
        chunks = await supabase.select(
            "document_chunks", columns="content,chunk_index",
            filters={"user_id": eq(user_id), "document_id": eq(did)},
            order="chunk_index.asc", limit=20,
        ) or []
        text = " ".join(c["content"] for c in chunks)[:_MAX_CHARS_PER_DOC]
        if text:
            parts.append(text)
            total += len(text)
        if total >= _MAX_TOTAL_CHARS:
            break
    return label, "\n\n---\n\n".join(parts)[:_MAX_TOTAL_CHARS]


async def generate(
    user_id: str, *, document_id: str | None = None, course_id: str | None = None,
    folder_id: str | None = None, max_cards: int = 15,
) -> dict[str, Any]:
    label, text = await _gather_source_text(
        user_id, document_id=document_id, course_id=course_id, folder_id=folder_id,
    )
    if not text.strip():
        return {
            "status": "error",
            "message": "Nothing to generate flashcards from -- no indexed document text found for that scope.",
        }

    max_cards = max(1, min(max_cards, _MAX_CARDS))
    prompt = f"""\
Extract the key terms and concepts from this material and turn them into
flashcards for active recall. Each card's front is a term or short
question, the back is a concise, accurate definition/answer (1-3 sentences)
grounded ONLY in the material below -- never invent a term or definition
that isn't actually supported by it.

Return at most {max_cards} cards, prioritizing the terms most central to
the material (skip incidental mentions). Return JSON:
{{"cards": [{{"front": "...", "back": "..."}}]}}

MATERIAL ("{label}"):
{text}"""
    data = await claude.complete_json(
        system=(
            "You are Atlas's flashcard generator. You extract accurate, concise "
            "study flashcards from real course material, never from general "
            "knowledge the material doesn't actually contain."
        ),
        prompt=prompt, max_tokens=2200,
    )
    cards = [
        {"front": (c.get("front") or "").strip(), "back": (c.get("back") or "").strip()}
        for c in (data.get("cards") or [])
        if (c.get("front") or "").strip() and (c.get("back") or "").strip()
    ][:max_cards]
    if not cards:
        return {"status": "error", "message": "Couldn't find any clear key terms in that material."}

    rows = [
        {
            "user_id": user_id, "front": c["front"], "back": c["back"], "source_label": label,
            "document_id": document_id, "course_id": course_id, "folder_id": folder_id,
        }
        for c in cards
    ]
    created = await supabase.insert("flashcards", rows)
    return {"status": "done", "count": len(created or rows), "source_label": label, "cards": created or rows}


async def review(user_id: str, flashcard_id: str, quality: int) -> dict[str, Any]:
    rows = await supabase.select(
        "flashcards", filters={"user_id": eq(user_id), "id": eq(flashcard_id)}, limit=1,
    ) or []
    if not rows:
        return {"status": "error", "message": "Flashcard not found."}
    card = rows[0]
    ease, reps, interval = knowledge_model.compute_sm2(
        quality,
        float(card.get("ease_factor") or 2.5),
        int(card.get("repetitions") or 0),
        int(card.get("interval_days") or 0),
    )
    now = datetime.now(timezone.utc)
    patch = {
        "ease_factor": ease,
        "repetitions": reps,
        "interval_days": interval,
        "last_reviewed_at": now.isoformat(),
        "next_review_at": (now + timedelta(days=interval)).isoformat(),
    }
    updated = await supabase.update(
        "flashcards", patch, filters={"user_id": eq(user_id), "id": eq(flashcard_id)},
    )
    return {"status": "done", "flashcard": updated[0] if updated else {**card, **patch}}
