"""Integration providers (Phase 2 scaffold).

Each provider knows how to pull courses, assignments, grades, announcements,
and files from an external LMS and normalize them into Atlas's schema. The
concrete API clients are intentionally left as clearly-marked stubs — they
require per-district credentials/OAuth and often unofficial endpoints — but the
orchestration, normalization, and persistence contract are fully defined here
so implementing a provider is a localized task.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase
from app.integrations.base import IntegrationProvider
from app.integrations.blackboard import BlackboardProvider
from app.integrations.powerschool import PowerSchoolProvider
from app.integrations.schoology import SchoologyProvider

PROVIDERS: dict[str, IntegrationProvider] = {
    "schoology": SchoologyProvider(),
    "powerschool": PowerSchoolProvider(),
    "blackboard": BlackboardProvider(),
}

# Vercel's `maxDuration` for the backend function is 300s (vercel.json —
# raised from 60s: a real account's sync kept genuinely needing more than
# 60s, even after logging in once instead of per course, running section
# syncs concurrently, and moving blocking calls off the event loop — the
# work itself just doesn't fit in 60s for an account with enough courses
# and materials). If a sync runs longer than the configured max, the
# platform hard-kills the process mid-`await` with no exception raised —
# `run_sync`'s except blocks never fire, and the row saved by
# `_set_status(..., "running")` is left stuck forever. Timing out here
# first, with margin to spare, means we always get to record a real status
# ourselves before the platform can silently do it for us.
SYNC_TIMEOUT_SECONDS = 270

# Safety net for anything that still manages to get stuck on "running" (e.g.
# an old row from before this timeout existed, or a kill that also cut off
# the timeout handler itself) — reconciled on every scheduled run.
STALE_RUNNING_MINUTES = 10


async def reconcile_stale_syncs(provider: str, *, stale_after_minutes: int = STALE_RUNNING_MINUTES) -> None:
    """Force-fail any row left on "running" well past a sync's own timeout."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
    try:
        await supabase.update(
            "integrations",
            {
                "status": "error",
                "last_error": (
                    f"Sync was interrupted (stuck on \"running\" past "
                    f"{stale_after_minutes} minutes) and was marked failed automatically."
                ),
            },
            filters={
                "provider": eq(provider),
                "status": eq("running"),
                "updated_at": f"lt.{cutoff}",
            },
        )
    except Exception:
        pass


async def run_sync_for_all(provider: str) -> dict[str, Any]:
    """Sync every user who has this provider connected & enabled — the entry
    point automated schedulers (Vercel Cron, n8n, …) call, since a scheduler
    has no logged-in user to scope a request to the way the normal
    `POST /integrations/{provider}/sync` endpoint does."""
    await reconcile_stale_syncs(provider)
    rows = await supabase.select(
        "integrations", columns="user_id",
        filters={"provider": eq(provider), "enabled": eq("true")},
    ) or []
    results = [
        {"user_id": row["user_id"], **await run_sync(provider, row["user_id"])}
        for row in rows
    ]
    return {
        "provider": provider,
        "synced": len(results),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


async def run_sync(provider: str, user_id: str) -> dict[str, Any]:
    """Sync `provider` for `user_id`. Manual ("Sync now") and scheduled
    (cron) triggers both come through here — including a manual retry
    clicked while a previous sync is still genuinely in flight. Two
    overlapping syncs racing the same authenticated Schoology session
    concurrently is exactly the kind of concurrency that turned out to be
    unreliable in production (see _SECTION_SYNC_CONCURRENCY's history), so
    the "running" transition is claimed with an atomic
    `UPDATE ... WHERE status != 'running'`: only one sync can be in flight
    per user+provider at a time, and a second attempt is rejected
    immediately with a clear message instead of silently interfering with
    the first."""
    impl = PROVIDERS[provider]
    claimed = await supabase.update(
        "integrations", {"status": "running"},
        filters={"user_id": eq(user_id), "provider": eq(provider), "status": "neq.running"},
    )
    if not claimed:
        existing = await supabase.select(
            "integrations", columns="status",
            filters={"user_id": eq(user_id), "provider": eq(provider)}, limit=1,
        )
        if existing:
            msg = "A sync is already running for this account — wait for it to finish, or use Cancel to clear it."
            return {"provider": provider, "status": "error", "detail": msg}
        # No integration row exists yet to claim (shouldn't normally happen —
        # every caller creates the row before syncing) — fall through so the
        # usual _set_status still records a status once one exists.
        await _set_status(user_id, provider, "running")
    try:
        result = await asyncio.wait_for(impl.sync(user_id), timeout=SYNC_TIMEOUT_SECONDS)
        if result.get("skipped"):
            result.setdefault("errors", []).append(
                f"{result['skipped']} item(s) had nothing real to save (an empty "
                "folder, a failed download, or a link with no real content) and "
                "were skipped rather than saved as empty placeholders — they'll be "
                "retried on the next sync."
            )
        await _set_status(user_id, provider, "success", None)
        return {"provider": provider, "status": "success", **result}
    except NotImplementedError as e:
        await _set_status(user_id, provider, "idle", str(e))
        return {"provider": provider, "status": "not_implemented", "detail": str(e)}
    except asyncio.TimeoutError:
        msg = f"Sync timed out after {SYNC_TIMEOUT_SECONDS}s and was aborted."
        await _set_status(user_id, provider, "error", msg)
        return {"provider": provider, "status": "error", "detail": msg}
    except Exception as e:  # noqa: BLE001
        await _set_status(user_id, provider, "error", str(e))
        return {"provider": provider, "status": "error", "detail": str(e)}


async def cancel_sync(provider: str, user_id: str) -> dict[str, Any]:
    """Let the user unstick their own integration instead of waiting out
    `reconcile_stale_syncs`'s 10-minute sweep (or a legitimately slow run
    they just want to give up on). There's no handle to the in-flight
    request itself to actually abort it — a Vercel invocation isn't
    cancellable via API — so this only clears the "running" status; if that
    request is still alive, its own eventual `_set_status` call will just
    overwrite this again. Only applies to a row currently "running" — a
    no-op (`canceled: False`) otherwise, e.g. the sync already finished."""
    rows = await supabase.update(
        "integrations",
        {"status": "idle", "last_error": "Sync canceled."},
        filters={"user_id": eq(user_id), "provider": eq(provider), "status": eq("running")},
    )
    return {"provider": provider, "status": "idle", "canceled": bool(rows)}


async def _set_status(user_id: str, provider: str, status: str, error: str | None = "") -> None:
    patch: dict[str, Any] = {"status": status}
    if status == "success":
        patch["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    if error is not None:
        patch["last_error"] = error
    try:
        await supabase.update(
            "integrations", patch,
            filters={"user_id": eq(user_id), "provider": eq(provider)},
        )
    except Exception:
        pass
