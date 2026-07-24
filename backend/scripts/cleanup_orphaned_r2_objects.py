"""One-off cleanup: remove R2 objects that no `documents` row references.

Why these can exist at all: a document row is supposed to be the only
reference to its R2 object, but a few historical edge cases could leave an
object behind with nothing pointing at it — most concretely, an upload that
actually succeeded on R2 but whose confirmation response the backend timed
out waiting for (fixed going forward by a longer R2 client read/write
timeout and a retry-on-resync path — see `SchoologyProvider._ingest_file`),
which used to look like "the file's gone" from the app even though it was
sitting in the bucket the whole time.

This does **not** touch anything currently queued in `pending_r2_deletions`
(a document deleted in the app within the last 24h — see
`app.services.storage_cleanup`) even though those rows aren't `documents`
rows either; skipping them is what keeps this script from finalizing a
delete that hasn't finished its grace period yet.

Usage (run with the same environment variables the deployed backend uses —
SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, ATLAS_STORAGE_BUCKET):

    python -m scripts.cleanup_orphaned_r2_objects            # dry run — lists what WOULD be deleted
    python -m scripts.cleanup_orphaned_r2_objects --confirm   # actually deletes them

Dry run is the default on purpose — this is a destructive, irreversible
operation (R2 has no trash/undelete) run directly against production data;
review the printed list before re-running with --confirm.
"""
from __future__ import annotations

import asyncio
import sys

from app.core.r2_client import r2
from app.core.supabase_client import supabase


_MAX_DOCUMENTS = 50_000  # see the pagination note below


async def _referenced_paths() -> set[str]:
    referenced: set[str] = set()

    # `app.core.supabase_client`'s `select()` wrapper only takes a `limit`,
    # no offset/cursor — a single request with a generous cap rather than
    # true pagination. Fine at this app's current scale; if `documents`
    # ever grows past `_MAX_DOCUMENTS`, this needs real paging (a `Range`
    # header via PostgREST) or it will silently miss rows and risk treating
    # a still-referenced object as orphaned.
    rows = await supabase.select(
        "documents", columns="storage_path",
        filters={"storage_path": "not.is.null"},
        limit=_MAX_DOCUMENTS,
    ) or []
    if len(rows) >= _MAX_DOCUMENTS:
        raise RuntimeError(
            f"Hit the {_MAX_DOCUMENTS}-row cap on `documents` — this script needs real "
            "pagination before it's safe to run again; refusing to proceed with a "
            "possibly-incomplete reference list."
        )
    for row in rows:
        path = row.get("storage_path")
        if path:
            referenced.add(path)

    pending = await supabase.select("pending_r2_deletions", columns="storage_path") or []
    for row in pending:
        path = row.get("storage_path")
        if path:
            referenced.add(path)

    return referenced


async def _all_object_keys() -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        page = await r2.list_objects(continuation_token=token)
        keys.extend(page["keys"])
        token = page["next_token"]
        if not token:
            break
    return keys


async def main() -> None:
    confirm = "--confirm" in sys.argv

    print("Loading referenced storage paths from Supabase...")
    referenced = await _referenced_paths()
    print(f"  {len(referenced)} paths currently referenced (documents + pending deletions).")

    print("Listing objects in the R2 bucket...")
    all_keys = await _all_object_keys()
    print(f"  {len(all_keys)} objects in the bucket.")

    orphans = [k for k in all_keys if k not in referenced]
    print(f"\n{len(orphans)} orphaned object(s) found.")
    for key in orphans:
        print(f"  {key}")

    if not orphans:
        print("\nNothing to do.")
        return

    if not confirm:
        print(
            f"\nDry run only — {len(orphans)} object(s) would be deleted. "
            "Re-run with --confirm to actually delete them."
        )
        return

    print(f"\nDeleting {len(orphans)} object(s)...")
    removed, failed = 0, 0
    for key in orphans:
        try:
            await r2.remove(key)
            removed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAILED to delete {key}: {e}")
    print(f"\nDone — removed {removed}, failed {failed}.")


if __name__ == "__main__":
    asyncio.run(main())
