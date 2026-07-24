"""Delayed R2 object deletion.

Deleting a document in the app removes its row immediately, but the
underlying R2 file is queued here instead of being removed inline — a
24-hour grace window before the original file is actually gone for good, in
case a delete needs to be undone. `documents.delete_document` queues the
object (see `queue_deletion`); a scheduled sweep
(`app/routers/documents.py`'s cron route) calls `purge_expired` once the
grace window has passed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.r2_client import r2
from app.core.supabase_client import eq, supabase

GRACE_PERIOD_HOURS = 24


async def queue_deletion(storage_path: str) -> None:
    await supabase.insert("pending_r2_deletions", {"storage_path": storage_path})


async def purge_expired() -> dict[str, Any]:
    """Actually remove from R2 every object queued more than
    `GRACE_PERIOD_HOURS` ago. Best-effort per object: a removal failure
    leaves that row in place for the next sweep to retry, rather than
    losing track of it."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=GRACE_PERIOD_HOURS)).isoformat()
    rows = await supabase.select(
        "pending_r2_deletions", filters={"requested_at": f"lt.{cutoff}"},
    ) or []
    removed, failed = 0, 0
    for row in rows:
        try:
            await r2.remove(row["storage_path"])
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        await supabase.delete("pending_r2_deletions", filters={"id": eq(row["id"])})
        removed += 1
    return {"removed": removed, "failed": failed, "checked": len(rows)}
