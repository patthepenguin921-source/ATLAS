"""Integrations (Phase 2 scaffold) — LMS connections & sync orchestration.

The actual provider clients (Schoology, PowerSchool, Blackboard, …) live in
`app.integrations`. This router manages connection records and triggers syncs.
Automation (n8n) can also call these endpoints on a schedule.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.integrations import PROVIDERS, run_sync
from app.integrations.powerschool import encrypt_credentials
from app.integrations.powerschool_client import PowerSchoolClient
from app.schemas import GenericBody, PowerSchoolConnectRequest

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _normalize_base_url(raw: str) -> str:
    base_url = raw.strip().rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    return base_url


@router.get("/providers")
async def providers():
    return [{"provider": name, "status": p.status} for name, p in PROVIDERS.items()]


@router.get("")
async def list_integrations(user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "integrations", filters={"user_id": eq(user.id)}, order="created_at.desc"
    )


@router.post("", status_code=201)
async def create_integration(body: GenericBody, user: CurrentUser = Depends(get_current_user)):
    data = body.data()
    provider = data.get("provider")
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Known: {list(PROVIDERS)}")
    row = {
        "user_id": user.id, "provider": provider,
        "display_name": data.get("display_name"),
        "config": data.get("config", {}),
        "enabled": data.get("enabled", True),
    }
    created = await supabase.insert("integrations", row, upsert=True)
    return created[0] if created else row


@router.post("/{provider}/sync")
async def sync_provider(provider: str, user: CurrentUser = Depends(get_current_user)):
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Known: {list(PROVIDERS)}")
    return await run_sync(provider, user.id)


@router.delete("/{provider}", status_code=204)
async def disconnect_integration(provider: str, user: CurrentUser = Depends(get_current_user)):
    await supabase.delete("integrations", filters={"user_id": eq(user.id), "provider": eq(provider)})
    return None


@router.get("/powerschool/probe")
async def probe_powerschool(base_url: str, user: CurrentUser = Depends(get_current_user)):
    """Fetch the PowerSchool login page and report what was found — no
    credentials sent — so a bad URL or an unsupported login flow (e.g. a
    district that requires SSO instead of a username/password form) can be
    diagnosed without server log access."""
    client = PowerSchoolClient(_normalize_base_url(base_url), "", "")
    try:
        return await client.probe_login_page()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not reach that portal URL: {e}") from e
    finally:
        await client.aclose()


@router.post("/powerschool/connect", status_code=201)
async def connect_powerschool(
    body: PowerSchoolConnectRequest, user: CurrentUser = Depends(get_current_user)
):
    """Save the portal URL + login and immediately run a first sync, so the
    caller finds out right away if the credentials/URL don't work."""
    base_url = _normalize_base_url(body.base_url)
    row = {
        "user_id": user.id,
        "provider": "powerschool",
        "display_name": body.display_name or "PowerSchool",
        "config": {"base_url": base_url},
        "secret_ref": encrypt_credentials(body.username, body.password),
        "enabled": True,
    }
    await supabase.insert("integrations", row, upsert=True)
    return await run_sync("powerschool", user.id)
