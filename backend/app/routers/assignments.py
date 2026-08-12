"""Assignment-specific actions that go beyond generic CRUD.

The generic CRUD router (see app.core.crud) already handles list/get/create/
update/delete for assignments. This router adds duplicate detection/merging
-- see app.services.assignment_dedupe -- for when the same real assignment
got entered or synced twice (most often PowerSchool and Schoology both
picking it up under slightly different titles).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.schemas import DismissDuplicateAssignmentsRequest, MergeAssignmentsRequest
from app.services import assignment_dedupe

router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.get("/possible-duplicates")
async def possible_duplicates(user: CurrentUser = Depends(get_current_user)):
    return await assignment_dedupe.find_possible_duplicates(user.id)


@router.post("/merge")
async def merge(body: MergeAssignmentsRequest, user: CurrentUser = Depends(get_current_user)):
    try:
        return await assignment_dedupe.merge_assignments(user.id, body.keep_id, body.discard_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/dismiss-duplicate", status_code=204)
async def dismiss_duplicate(
    body: DismissDuplicateAssignmentsRequest, user: CurrentUser = Depends(get_current_user)
):
    await assignment_dedupe.dismiss_duplicate(user.id, body.assignment_id_a, body.assignment_id_b)
    return None
