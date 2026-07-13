"""Course-specific actions that go beyond generic CRUD.

The generic CRUD router (see app.core.crud) already handles list/get/create/
update/delete for courses. This router adds the semester-split action: turning
one course into two linked rows (e.g. an HN Prep Lab semester weighted 5.5 and
an AP semester weighted 6.0) that track grades and GPA independently.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import SplitSemestersRequest

router = APIRouter(prefix="/courses", tags=["courses"])

# Columns copied verbatim from the original course onto its new S2 sibling.
_COPY_FIELDS = (
    "name", "code", "subject", "teacher_id", "term_id",
    "credit_hours", "color", "period", "room",
)


@router.get("/{course_id}/semesters")
async def course_semesters(course_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return every course linked to the same class (both semester halves)."""
    rows = await supabase.select(
        "courses", filters={"user_id": eq(user.id), "id": eq(course_id)}, limit=1
    )
    if not rows:
        raise HTTPException(404, "Not found")
    group = rows[0].get("linked_course_id") or course_id
    linked = await supabase.select(
        "courses",
        filters={"user_id": eq(user.id), "linked_course_id": eq(group)},
        order="semester.asc",
    ) or []
    return linked


@router.post("/{course_id}/split-semesters")
async def split_semesters(
    course_id: str,
    body: SplitSemestersRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Split a full-year course into linked S1 / S2 rows.

    The original row becomes semester 1; a new sibling row is created for
    semester 2. Both point at the original id via ``linked_course_id`` so the
    UI can group them and let the student jump between halves. Idempotent-ish:
    an already-split course just returns its existing halves.
    """
    rows = await supabase.select(
        "courses", filters={"user_id": eq(user.id), "id": eq(course_id)}, limit=1
    )
    if not rows:
        raise HTTPException(404, "Not found")
    course = rows[0]

    if course.get("linked_course_id"):
        return await course_semesters(course_id, user)

    # Semester 1 = the original row.
    s1_patch = {
        "semester": "s1",
        "linked_course_id": course_id,
        "has_hn_prep_lab": body.s1_has_hn_prep_lab,
    }
    if body.s1_level:
        s1_patch["course_level"] = body.s1_level
    await supabase.update(
        "courses", s1_patch, filters={"user_id": eq(user.id), "id": eq(course_id)}
    )

    # Semester 2 = a fresh linked row.
    sibling = {k: course.get(k) for k in _COPY_FIELDS}
    sibling.update({
        "user_id": user.id,
        "semester": "s2",
        "linked_course_id": course_id,
        "course_level": body.s2_level,
        "has_ap_prep_lab": body.s2_has_ap_prep_lab,
        "sort_order": course.get("sort_order", 0),
    })
    await supabase.insert("courses", sibling)

    return await course_semesters(course_id, user)
