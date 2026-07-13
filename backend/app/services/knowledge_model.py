"""Student Knowledge Model — Atlas's evolving estimate of understanding.

Maintains, per concept: confidence, mastery, retention, and a spaced-repetition
schedule (SM-2 style). Updated automatically after quizzes, assignments, tests.
Retention decays over time following a forgetting curve so Atlas can predict
*when* something will be forgotten and surface it for review beforehand.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase


def _retention(interval_days: int, days_since_review: float) -> float:
    """Forgetting-curve retention estimate R = exp(-t / S)."""
    stability = max(1.0, interval_days)  # more spacing => slower decay
    return round(math.exp(-days_since_review / (stability * 1.6)), 3)


async def _get_or_create(user_id: str, concept_id: str) -> dict[str, Any]:
    rows = await supabase.select(
        "student_knowledge",
        filters={"user_id": eq(user_id), "concept_id": eq(concept_id)},
        limit=1,
    )
    if rows:
        return rows[0]
    created = await supabase.insert(
        "student_knowledge",
        {"user_id": user_id, "concept_id": concept_id},
    )
    return created[0]


async def review(
    user_id: str, concept_id: str, quality: int, *, confidence: float | None = None
) -> dict[str, Any]:
    """Record a review outcome and reschedule (SM-2).

    quality: 0..5 (0 = total blackout, 5 = perfect recall).
    """
    quality = max(0, min(5, quality))
    k = await _get_or_create(user_id, concept_id)

    ease = float(k.get("ease_factor") or 2.5)
    reps = int(k.get("repetitions") or 0)
    interval = int(k.get("interval_days") or 0)

    if quality < 3:
        reps = 0
        interval = 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = round(interval * ease)
    ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))

    now = datetime.now(timezone.utc)
    next_review = now + timedelta(days=interval)
    mastery = round(min(1.0, (float(k.get("mastery") or 0.0) * 0.6) + (quality / 5.0) * 0.4), 3)

    patch = {
        "ease_factor": round(ease, 3),
        "repetitions": reps,
        "interval_days": interval,
        "last_reviewed_at": now.isoformat(),
        "next_review_at": next_review.isoformat(),
        "predicted_forget_at": (now + timedelta(days=max(1, round(interval * 1.6)))).isoformat(),
        "retention": 1.0,
        "mastery": mastery,
        "evidence_count": int(k.get("evidence_count") or 0) + 1,
    }
    if confidence is not None:
        patch["confidence"] = round(max(0.0, min(1.0, confidence)), 3)

    updated = await supabase.update(
        "student_knowledge", patch,
        filters={"user_id": eq(user_id), "concept_id": eq(concept_id)},
    )
    return updated[0]


async def observe_grade(user_id: str, concept_ids: list[str], percentage: float) -> None:
    """Translate a graded outcome into a review signal for its concepts."""
    quality = max(0, min(5, round(percentage / 20)))  # 100% -> 5, 60% -> 3
    for cid in concept_ids:
        await review(user_id, cid, quality)


async def refresh_retention(user_id: str) -> int:
    """Recompute retention for all of a user's tracked concepts (decay)."""
    rows = await supabase.select(
        "student_knowledge",
        columns="concept_id,interval_days,last_reviewed_at",
        filters={"user_id": eq(user_id)},
    ) or []
    now = datetime.now(timezone.utc)
    updated = 0
    for r in rows:
        if not r.get("last_reviewed_at"):
            continue
        last = datetime.fromisoformat(r["last_reviewed_at"].replace("Z", "+00:00"))
        days = (now - last).total_seconds() / 86400.0
        ret = _retention(int(r.get("interval_days") or 1), days)
        await supabase.update(
            "student_knowledge", {"retention": ret},
            filters={"user_id": eq(user_id), "concept_id": eq(r["concept_id"])},
        )
        updated += 1
    return updated
