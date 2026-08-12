"""Detects assignments that likely represent the same real-world assignment
entered or synced twice -- most commonly PowerSchool and Schoology both
picking up the same assignment under slightly different titles -- so the
student can review and merge them from the assignments page instead of
double-counting it (two due dates, two "missing" badges, a grade sitting on
the wrong row) forever.

Deliberately conservative: two assignments only get suggested when they're
in the *same course*, have a *high* title similarity, and (when both have
one) a due date within a couple of days of each other. A missed duplicate
just means one extra row on the page; a wrong merge silently combines two
different assignments' grades and due dates, which is much worse.
"""
from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from app.core.supabase_client import eq, supabase

# Tables that reference an assignment and must move onto the kept row
# before the discarded one is deleted -- several of these `on delete
# cascade`/`set null` on the assignments FK, so skipping this would silently
# drop a grade or orphan a calendar event/reminder on every merge.
# `assignment_concepts` is deliberately excluded: nothing in the app writes
# to it yet, so there's nothing to carry over.
_DEPENDENT_TABLES = (
    "grades", "calendar_events", "study_sessions", "documents", "mistakes", "reminders",
)

# Fields copied onto the kept row from the discarded one when the kept row
# doesn't already have a value -- so merging never loses detail that only
# made it onto one side (e.g. Schoology's sync filled in points_possible,
# PowerSchool's didn't).
_FILL_FIELDS = (
    "description", "notes", "points_possible", "weight", "difficulty",
    "estimated_minutes", "folder_id", "learning_objectives", "tags",
)

# Below this, two titles are treated as different assignments even in the
# same course with a matching due date -- recurring weekly work ("Homework
# 3" vs "Homework 4") is the main false-positive risk, so this stays high.
_TITLE_SIMILARITY_THRESHOLD = 0.82

# Due dates further apart than this are treated as different assignments
# even with a near-identical title -- guards against merging two distinct
# weeks of a recurring assignment name that happen to also drift in title.
_DUE_DATE_WINDOW_DAYS = 2


def _normalize_title(title: str | None) -> str:
    return " ".join((title or "").lower().split())


def _title_similarity(a: str | None, b: str | None) -> float:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _looks_like_duplicate(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Similarity score (0..1) if `a`/`b` look like the same real assignment
    entered/synced twice, else None."""
    if not a.get("course_id") or a["course_id"] != b.get("course_id"):
        return None
    similarity = _title_similarity(a.get("title"), b.get("title"))
    if similarity < _TITLE_SIMILARITY_THRESHOLD:
        return None
    due_a, due_b = _parse_due(a.get("due_date")), _parse_due(b.get("due_date"))
    if due_a and due_b and abs((due_a - due_b).days) > _DUE_DATE_WINDOW_DAYS:
        return None
    return similarity


def _fill_count(a: dict[str, Any]) -> int:
    return sum(1 for field in _FILL_FIELDS if a.get(field) not in (None, "", []))


def _pick_keeper(a: dict[str, Any], b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Which of the pair to suggest keeping: whichever already has more
    filled-in detail, falling back to the earlier-created row (usually
    whichever source synced it first) as a stable tiebreaker. Only a
    suggestion -- the student picks which side to actually keep."""
    fa, fb = _fill_count(a), _fill_count(b)
    if fa != fb:
        return (a, b) if fa > fb else (b, a)
    return (a, b) if (a.get("created_at") or "") <= (b.get("created_at") or "") else (b, a)


async def find_possible_duplicates(user_id: str) -> list[dict[str, Any]]:
    """Every pair of the student's own assignments that look like the same
    real assignment entered/synced twice, most-confident first, excluding
    any pair already dismissed as not-a-duplicate."""
    assignments = await supabase.select(
        "assignments",
        columns="id,title,course_id,due_date,status,category,external_source,"
                "points_possible,description,notes,weight,difficulty,"
                "estimated_minutes,folder_id,created_at",
        filters={"user_id": eq(user_id)},
        limit=1000,
    ) or []
    dismissed = await supabase.select(
        "assignment_duplicate_dismissals",
        columns="assignment_id_a,assignment_id_b",
        filters={"user_id": eq(user_id)},
        limit=1000,
    ) or []
    dismissed_pairs = {(d["assignment_id_a"], d["assignment_id_b"]) for d in dismissed}

    candidates: list[dict[str, Any]] = []
    for a, b in combinations(assignments, 2):
        if _pair_key(a["id"], b["id"]) in dismissed_pairs:
            continue
        score = _looks_like_duplicate(a, b)
        if score is None:
            continue
        keep, discard = _pick_keeper(a, b)
        candidates.append({
            "score": round(score, 3),
            "suggested_keep_id": keep["id"],
            "suggested_discard_id": discard["id"],
            "assignments": [a, b],
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


async def merge_assignments(user_id: str, keep_id: str, discard_id: str) -> dict[str, Any]:
    """Merges `discard_id` into `keep_id`: moves every dependent row (grades,
    calendar events, study sessions, attached documents, mistakes,
    reminders) onto the kept assignment, fills in any of the kept row's
    blank fields from the discarded one, then deletes the discarded row.
    Never destructive to grade/attachment data -- the whole point of a
    merge is that nothing synced onto either side gets lost."""
    if keep_id == discard_id:
        raise ValueError("Can't merge an assignment with itself.")
    rows = await supabase.select(
        "assignments",
        filters={"user_id": eq(user_id), "id": f"in.({keep_id},{discard_id})"},
        limit=2,
    ) or []
    by_id = {r["id"]: r for r in rows}
    if keep_id not in by_id or discard_id not in by_id:
        raise LookupError("One or both assignments were not found.")
    keep, discard = by_id[keep_id], by_id[discard_id]

    for table in _DEPENDENT_TABLES:
        await supabase.update(
            table, {"assignment_id": keep_id},
            filters={"user_id": eq(user_id), "assignment_id": eq(discard_id)},
        )

    fill = {
        field: discard[field] for field in _FILL_FIELDS
        if keep.get(field) in (None, "", []) and discard.get(field) not in (None, "", [])
    }
    if fill:
        updated = await supabase.update(
            "assignments", fill, filters={"user_id": eq(user_id), "id": eq(keep_id)},
        )
        keep = updated[0] if updated else keep

    await supabase.delete("assignments", filters={"user_id": eq(user_id), "id": eq(discard_id)})
    return keep


async def dismiss_duplicate(user_id: str, assignment_id_a: str, assignment_id_b: str) -> None:
    """Remembers that this pair isn't actually a duplicate, so it stops
    being suggested. Stored with a canonical (min, max) id order (see
    `_pair_key`) so the same pair is one row no matter which order a later
    scan happens to compare the two assignments in."""
    a, b = _pair_key(assignment_id_a, assignment_id_b)
    await supabase.insert(
        "assignment_duplicate_dismissals",
        {"user_id": user_id, "assignment_id_a": a, "assignment_id_b": b},
        upsert=True, on_conflict="user_id,assignment_id_a,assignment_id_b",
    )
