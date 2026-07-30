"""Folder helpers shared by the folders router and the Archivist's
auto-sort pass — get-or-create logic that both need, kept in one place so
"reuse this folder if it already exists" is never reimplemented twice.
"""
from __future__ import annotations

from typing import Any

from app.core.supabase_client import eq, supabase


async def get_or_create_general_folder(user_id: str) -> str:
    """The singleton "General" root (course_id null, is_general true) for
    documents that don't belong to any class — created lazily on first use
    rather than at signup, since most users may never need it."""
    existing = await supabase.select(
        "folders", columns="id",
        filters={"user_id": eq(user_id), "is_general": eq("true")}, limit=1,
    )
    if existing:
        return existing[0]["id"]
    try:
        created = await supabase.insert(
            "folders", {"user_id": user_id, "name": "General", "is_general": True},
        )
        return created[0]["id"]
    except Exception:
        # Lost a race with another request creating the same singleton —
        # the unique partial index (idx_folders_one_general_per_user)
        # guarantees only one exists, so just fetch it.
        existing = await supabase.select(
            "folders", columns="id",
            filters={"user_id": eq(user_id), "is_general": eq("true")}, limit=1,
        )
        return existing[0]["id"]


async def find_or_create_folder(
    user_id: str, name: str, *, course_id: str | None, parent_folder_id: str | None,
) -> str | None:
    """Reuse an existing folder by name (case-insensitive) within the given
    scope — a course's top level, or a parent folder — creating one only
    when nothing matches. Used by the Archivist's auto-sort so re-enriching
    a batch of documents about the same unit doesn't spawn a duplicate
    folder per document."""
    clean = (name or "").strip()
    if not clean:
        return None
    filters: dict[str, Any] = {"user_id": eq(user_id)}
    filters["course_id"] = eq(course_id) if course_id else "is.null"
    filters["parent_folder_id"] = eq(parent_folder_id) if parent_folder_id else "is.null"
    existing = await supabase.select("folders", columns="id,name", filters=filters) or []
    for f in existing:
        if (f.get("name") or "").strip().lower() == clean.lower():
            return f["id"]
    created = await supabase.insert("folders", {
        "user_id": user_id, "name": clean,
        "course_id": course_id, "parent_folder_id": parent_folder_id,
    })
    return created[0]["id"] if created else None


async def top_level_folders(
    user_id: str, *, course_id: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """The folders the Archivist is allowed to auto-file into: a course's
    top-level folders, or General's top-level folders when there's no
    course. AI auto-sort only ever reaches one level deep — deeper
    subfolders are for the student to organize by hand. Returns
    ``(candidates, parent_folder_id)`` — the parent id a newly-created
    folder should be filed under (None for a course's own top level)."""
    parent_folder_id = None if course_id else await get_or_create_general_folder(user_id)
    filters: dict[str, Any] = {
        "user_id": eq(user_id),
        "course_id": eq(course_id) if course_id else "is.null",
        "parent_folder_id": eq(parent_folder_id) if parent_folder_id else "is.null",
    }
    candidates = await supabase.select("folders", columns="id,name", filters=filters) or []
    return candidates, parent_folder_id
