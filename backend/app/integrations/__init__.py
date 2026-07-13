"""Integration providers (Phase 2 scaffold).

Each provider knows how to pull courses, assignments, grades, announcements,
and files from an external LMS and normalize them into Atlas's schema. The
concrete API clients are intentionally left as clearly-marked stubs — they
require per-district credentials/OAuth and often unofficial endpoints — but the
orchestration, normalization, and persistence contract are fully defined here
so implementing a provider is a localized task.
"""
from __future__ import annotations

from datetime import datetime, timezone
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


async def run_sync(provider: str, user_id: str) -> dict[str, Any]:
    impl = PROVIDERS[provider]
    await _set_status(user_id, provider, "running")
    try:
        result = await impl.sync(user_id)
        await _set_status(user_id, provider, "success", None)
        return {"provider": provider, "status": "success", **result}
    except NotImplementedError as e:
        await _set_status(user_id, provider, "idle", str(e))
        return {"provider": provider, "status": "not_implemented", "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        await _set_status(user_id, provider, "error", str(e))
        return {"provider": provider, "status": "error", "detail": str(e)}


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
