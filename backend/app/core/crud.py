"""Generic user-scoped CRUD router factory.

Produces list/get/create/update/delete endpoints for a table, always scoping
to the authenticated user and whitelisting writable columns (so a client can
never set another user's id or spoof server-managed fields).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import GenericBody


def make_crud_router(
    *,
    table: str,
    prefix: str,
    tag: str,
    writable: set[str],
    default_order: str = "created_at.desc",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k in writable}

    @router.get("")
    async def list_rows(
        limit: int = 100,
        order: str | None = None,
        course_id: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ):
        filters = {"user_id": eq(user.id)}
        if course_id:
            filters["course_id"] = eq(course_id)
        return await supabase.select(
            table, filters=filters, order=order or default_order, limit=limit
        )

    @router.get("/{row_id}")
    async def get_row(row_id: str, user: CurrentUser = Depends(get_current_user)):
        rows = await supabase.select(
            table, filters={"user_id": eq(user.id), "id": eq(row_id)}, limit=1
        )
        if not rows:
            raise HTTPException(404, "Not found")
        return rows[0]

    @router.post("", status_code=201)
    async def create_row(body: GenericBody, user: CurrentUser = Depends(get_current_user)):
        payload = _clean(body.data())
        payload["user_id"] = user.id
        created = await supabase.insert(table, payload)
        return created[0] if created else None

    @router.patch("/{row_id}")
    async def update_row(
        row_id: str, body: GenericBody, user: CurrentUser = Depends(get_current_user)
    ):
        payload = _clean(body.data())
        if not payload:
            raise HTTPException(400, "No writable fields provided")
        updated = await supabase.update(
            table, payload, filters={"user_id": eq(user.id), "id": eq(row_id)}
        )
        if not updated:
            raise HTTPException(404, "Not found")
        return updated[0]

    @router.delete("/{row_id}", status_code=204)
    async def delete_row(row_id: str, user: CurrentUser = Depends(get_current_user)):
        await supabase.delete(table, filters={"user_id": eq(user.id), "id": eq(row_id)})
        return None

    return router
