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
import time
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
# the timeout handler itself) — reconciled on every scheduled run, on every
# "Sync now" attempt, and on every page load (see run_sync and
# list_integrations). 6 minutes is SYNC_TIMEOUT_SECONDS (4.5 min) plus a
# couple minutes' margin — enough that a sync that's genuinely still running
# normally won't be reconciled out from under itself, but not so long that a
# hard-killed one looks permanently stuck.
STALE_RUNNING_MINUTES = 6

# Per-chunk time budget for a provider that supports resumable syncing (only
# Schoology's materials-only path does — see SchoologyProvider.sync). A real
# account's course count can push the *whole* sync past a single request's
# safe duration even though each individual course is fast (see
# run_sync_step's docstring), so a chunk does as much as fits in this budget
# and reports back "more to do" instead of blocking the caller for the whole
# thing. Comfortably under SYNC_TIMEOUT_SECONDS and any reasonable proxy/
# browser patience.
SYNC_CHUNK_SECONDS = 90


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


async def _is_resumable(provider: str, user_id: str) -> bool:
    """True when this provider+user has a chunked sync genuinely paused
    mid-cycle (see SchoologyProvider._save_sync_progress) — lets
    `run_sync_step` let the next chunk through even though status is still
    "running", without that turning into a loophole for a second, truly
    concurrent sync: it also requires status to still be "running", so a
    cycle `reconcile_stale_syncs` already force-failed (status flipped to
    "error") goes through the normal claim instead — which the provider
    will *still* resume via its own leftover `config._sync_progress`, just
    with the row correctly reclaimed first."""
    rows = await supabase.select(
        "integrations", columns="status,config",
        filters={"user_id": eq(user_id), "provider": eq(provider)}, limit=1,
    )
    if not rows:
        return False
    row = rows[0]
    return row.get("status") == "running" and bool((row.get("config") or {}).get("_sync_progress"))


async def _claim(provider: str, user_id: str) -> dict[str, Any] | None:
    """Atomically claim this provider+user's row for a new sync (`status !=
    'running' -> 'running'`) — the only way to start a *new* sync attempt,
    so two overlapping attempts racing the same authenticated Schoology
    session never happen (see _SECTION_SYNC_CONCURRENCY's history). Returns
    an error result if someone else's sync already holds it, else None."""
    claimed = await supabase.update(
        "integrations", {"status": "running"},
        filters={"user_id": eq(user_id), "provider": eq(provider), "status": "neq.running"},
    )
    if claimed:
        return None
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
    return None


async def _run_chunk(provider: str, user_id: str, impl: IntegrationProvider) -> dict[str, Any]:
    """Run one deadline-bounded chunk of `impl.sync` and record whatever it
    implies. Shared by `run_sync_step` (returns after exactly one chunk) and
    `run_sync` (loops this until the sync is genuinely done)."""
    chunk_timeout = SYNC_CHUNK_SECONDS + 60  # margin for one slow item within the chunk
    deadline = time.monotonic() + SYNC_CHUNK_SECONDS
    try:
        result = await asyncio.wait_for(impl.sync(user_id, deadline=deadline), timeout=chunk_timeout)
        if result.get("continue"):
            # More chunks remain — status stays "running"; `updated_at`
            # already got refreshed by the provider's own progress-saving
            # write, which is what keeps this from looking stale to
            # reconcile_stale_syncs while chunks are actively progressing.
            return {"provider": provider, "status": "running", **result}
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
        msg = f"This step of the sync timed out after {chunk_timeout}s and was aborted — try syncing again."
        await _set_status(user_id, provider, "error", msg)
        return {"provider": provider, "status": "error", "detail": msg}
    except Exception as e:  # noqa: BLE001
        await _set_status(user_id, provider, "error", str(e))
        return {"provider": provider, "status": "error", "detail": str(e)}


async def run_sync_step(provider: str, user_id: str) -> dict[str, Any]:
    """Run exactly one chunk of `provider`'s sync for `user_id` and return
    right away — the entry point for the manual "Sync now" button. A real
    account's sync can take several chunks to finish (see
    SYNC_CHUNK_SECONDS): holding one HTTP request open for the whole thing
    is the fragile pattern (a dropped WiFi, laptop sleep, a proxy's idle
    timeout) that made "Sync now" unreliable in the first place. The
    frontend calls this repeatedly while the response says
    `status: "running"`, and stops once it says anything else.

    Also reconciles this provider's stale "running" rows first — without
    this, a row left stuck by a hard-killed run (see SYNC_TIMEOUT_SECONDS's
    docstring) would only ever get cleared by the next scheduled
    `run_sync_for_all` sweep, up to 12 hours away; a manual "Sync now"
    click never used to trigger it, so a stuck sync looked permanent until
    someone clicked Cancel by hand."""
    await reconcile_stale_syncs(provider)
    impl = PROVIDERS[provider]
    if not await _is_resumable(provider, user_id):
        error = await _claim(provider, user_id)
        if error:
            return error
    return await _run_chunk(provider, user_id, impl)


async def run_sync(provider: str, user_id: str) -> dict[str, Any]:
    """Sync `provider` for `user_id`, blocking until it's genuinely done
    (success/error/not_implemented) instead of returning after one chunk —
    used by the cron sweep and the connect flow, both of which want one
    definitive answer rather than the chunk-by-chunk protocol
    `run_sync_step` uses for the browser. Loops `_run_chunk` internally for
    a provider that chunks (see SchoologyProvider.sync); a provider that
    doesn't just finishes on the first one, same as before."""
    await reconcile_stale_syncs(provider)
    impl = PROVIDERS[provider]
    error = await _claim(provider, user_id)
    if error:
        return error

    overall_deadline = time.monotonic() + SYNC_TIMEOUT_SECONDS
    while True:
        result = await _run_chunk(provider, user_id, impl)
        if result["status"] != "running":
            return result
        if time.monotonic() >= overall_deadline:
            msg = f"Sync timed out after {SYNC_TIMEOUT_SECONDS}s and was aborted."
            await _set_status(user_id, provider, "error", msg)
            return {"provider": provider, "status": "error", "detail": msg}


async def cancel_sync(provider: str, user_id: str) -> dict[str, Any]:
    """Let the user unstick their own integration instead of waiting out
    `reconcile_stale_syncs`'s STALE_RUNNING_MINUTES sweep (or a legitimately
    slow run they just want to give up on). There's no handle to the in-flight
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
