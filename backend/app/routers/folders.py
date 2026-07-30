"""Folders — per-class (or "General") subfolders for topics/units.

A class already acts as a top-level divider via `courses`; a folder is an
optional subfolder *within* one class's documents (or within the singleton
"General" tree, for documents that don't belong to any class) -- created
either by hand or automatically by the Archivist (see `app.agents.archivist`
and `app.services.folders`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import FolderCreateRequest, FolderPatchRequest
from app.services.folders import get_or_create_general_folder

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("/general")
async def general_folder(user: CurrentUser = Depends(get_current_user)):
    """Get-or-create the student's singleton "General" root -- the divider
    for documents that don't belong to any class."""
    folder_id = await get_or_create_general_folder(user.id)
    rows = await supabase.select(
        "folders", filters={"user_id": eq(user.id), "id": eq(folder_id)}, limit=1
    )
    return rows[0] if rows else None


@router.get("")
async def list_folders(
    course_id: str | None = None,
    general: bool = False,
    parent_folder_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Every folder in one scope -- a class (`course_id`) or "General"
    (`general=true`) -- flat, ordered for a client-side tree. Pass
    `parent_folder_id` too to narrow to just one folder's direct children.
    """
    if not course_id and not general:
        raise HTTPException(422, "Pass course_id or general=true.")
    filters: dict[str, str] = {
        "user_id": eq(user.id),
        "course_id": eq(course_id) if course_id else "is.null",
    }
    if parent_folder_id:
        filters["parent_folder_id"] = eq(parent_folder_id)
    folders = await supabase.select(
        "folders", filters=filters, order="sort_order.asc,name.asc",
    ) or []
    if general:
        # "General" itself never shows up as one of its own children.
        folders = [f for f in folders if not f.get("is_general")]
    return folders


@router.get("/{folder_id}")
async def get_folder(folder_id: str, user: CurrentUser = Depends(get_current_user)):
    rows = await supabase.select(
        "folders", filters={"user_id": eq(user.id), "id": eq(folder_id)}, limit=1
    )
    if not rows:
        raise HTTPException(404, "Not found")
    return rows[0]


async def _own_folder(user_id: str, folder_id: str) -> dict:
    rows = await supabase.select(
        "folders", filters={"user_id": eq(user_id), "id": eq(folder_id)}, limit=1
    )
    if not rows:
        raise HTTPException(404, "Parent folder not found")
    return rows[0]


@router.post("", status_code=201)
async def create_folder(body: FolderCreateRequest, user: CurrentUser = Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "A folder needs a name.")

    course_id = body.course_id
    parent_folder_id = body.parent_folder_id
    if parent_folder_id:
        # A subfolder's scope (class, or General) is inherited from its
        # parent -- any course_id passed alongside a parent is ignored so
        # the two can never disagree.
        parent = await _own_folder(user.id, parent_folder_id)
        course_id = parent.get("course_id")
    elif course_id:
        owned = await supabase.select(
            "courses", columns="id", filters={"user_id": eq(user.id), "id": eq(course_id)}, limit=1,
        )
        if not owned:
            raise HTTPException(404, "Course not found")
    else:
        # No class and no parent -- this document doesn't belong to a
        # specific class, so it's a top-level folder under "General".
        parent_folder_id = await get_or_create_general_folder(user.id)

    created = await supabase.insert("folders", {
        "user_id": user.id, "name": name,
        "course_id": course_id, "parent_folder_id": parent_folder_id,
    })
    return created[0] if created else None


def _descendant_ids(folder_id: str, children_by_parent: dict[str, list[str]]) -> set[str]:
    """Every folder nested (at any depth) under `folder_id`."""
    ids: set[str] = set()
    frontier = list(children_by_parent.get(folder_id, []))
    while frontier:
        child_id = frontier.pop()
        if child_id in ids:
            continue  # defensive: never loop forever on a corrupt chain
        ids.add(child_id)
        frontier.extend(children_by_parent.get(child_id, []))
    return ids


@router.patch("/{folder_id}")
async def update_folder(
    folder_id: str, body: FolderPatchRequest, user: CurrentUser = Depends(get_current_user),
):
    folder = await _own_folder(user.id, folder_id)
    if folder.get("is_general"):
        raise HTTPException(400, "The General folder can't be renamed or moved.")

    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No fields to update.")

    if "name" in patch:
        name = (patch["name"] or "").strip()
        if not name:
            raise HTTPException(422, "A folder needs a name.")
        patch["name"] = name

    if "parent_folder_id" in patch:
        new_parent_id = patch["parent_folder_id"]
        if new_parent_id == folder_id:
            raise HTTPException(422, "A folder can't be its own parent.")
        if new_parent_id:
            new_parent = await _own_folder(user.id, new_parent_id)
            if new_parent.get("course_id") != folder.get("course_id"):
                raise HTTPException(422, "Can't move a folder into a different class.")
            # Prevent creating a cycle (moving a folder under its own descendant).
            all_folders = await supabase.select(
                "folders", columns="id,parent_folder_id",
                filters={"user_id": eq(user.id)},
            ) or []
            children_by_parent: dict[str, list[str]] = {}
            for f in all_folders:
                if f.get("parent_folder_id"):
                    children_by_parent.setdefault(f["parent_folder_id"], []).append(f["id"])
            if new_parent_id in _descendant_ids(folder_id, children_by_parent):
                raise HTTPException(422, "Can't move a folder into its own subfolder.")
        # Moving to root (new_parent_id falsy) just stays within the same
        # class/General scope -- course_id is untouched either way.

    updated = await supabase.update(
        "folders", patch, filters={"user_id": eq(user.id), "id": eq(folder_id)}
    )
    if not updated:
        raise HTTPException(404, "Not found")
    return updated[0]


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(folder_id: str, user: CurrentUser = Depends(get_current_user)):
    folder = await _own_folder(user.id, folder_id)
    if folder.get("is_general"):
        raise HTTPException(400, "The General folder can't be deleted.")
    # Subfolders cascade-delete (parent_folder_id FK), and any document
    # filed in this folder or one of its subfolders falls back to its
    # class's top level automatically (documents.folder_id ON DELETE SET NULL).
    await supabase.delete("folders", filters={"user_id": eq(user.id), "id": eq(folder_id)})
    return None
