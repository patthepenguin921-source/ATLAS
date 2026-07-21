"""Integrations (Phase 2 scaffold) — LMS connections & sync orchestration.

The actual provider clients (Schoology, PowerSchool, Blackboard, …) live in
`app.integrations`. This router manages connection records and triggers syncs.
Automation (n8n) can also call these endpoints on a schedule.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.integrations import PROVIDERS, run_sync, run_sync_for_all
from app.integrations.powerschool import encrypt_credentials, encrypt_session_cookie
from app.integrations.powerschool_client import PowerSchoolClient
from app.integrations.schoology import SchoologyProvider, encrypt_api_key
from app.integrations.schoology_client import API_BASE as SCHOOLOGY_API_BASE
from app.integrations.schoology_client import SchoologyAuthError
from app.schemas import (
    GenericBody,
    PowerSchoolConnectRequest,
    PowerSchoolConnectSessionRequest,
    SchoologyConnectRequest,
)

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
    created = await supabase.insert("integrations", row, upsert=True, on_conflict="user_id,provider")
    return created[0] if created else row


@router.post("/{provider}/sync")
async def sync_provider(provider: str, user: CurrentUser = Depends(get_current_user)):
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Known: {list(PROVIDERS)}")
    return await run_sync(provider, user.id)


def _check_cron_secret(request: Request) -> None:
    """Auth for unattended scheduler calls — no user session exists, so this
    checks a shared secret instead of a bearer JWT. Accepts either the
    `Authorization: Bearer <secret>` header Vercel Cron sends automatically
    when `CRON_SECRET` is set, or a plain `X-Cron-Secret` header for other
    schedulers (n8n, curl, …)."""
    if not settings.atlas_cron_secret:
        raise HTTPException(
            503, "Automated sync isn't configured — set ATLAS_CRON_SECRET on the backend."
        )
    auth = request.headers.get("authorization") or ""
    provided = (
        auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else
        request.headers.get("x-cron-secret") or ""
    )
    if not provided or not hmac.compare_digest(provided, settings.atlas_cron_secret):
        raise HTTPException(401, "Bad or missing cron secret.")


async def _cron_sync_provider(provider: str, request: Request):
    """Automated sync trigger for schedulers (Vercel Cron, n8n, …) — runs the
    given provider's sync for every user who has it connected & enabled.
    Secured by ATLAS_CRON_SECRET instead of a user session; see
    `_check_cron_secret`."""
    _check_cron_secret(request)
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Known: {list(PROVIDERS)}")
    return await run_sync_for_all(provider)


# GET: Vercel Cron Jobs always invoke via GET. POST: kept for n8n/curl/other
# schedulers that prefer it — both do the same thing.
@router.get("/cron/{provider}/sync")
async def cron_sync_provider_get(provider: str, request: Request):
    return await _cron_sync_provider(provider, request)


@router.post("/cron/{provider}/sync")
async def cron_sync_provider_post(provider: str, request: Request):
    return await _cron_sync_provider(provider, request)


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
        "config": {"base_url": base_url, "auth_mode": "password"},
        "secret_ref": encrypt_credentials(body.username, body.password),
        "enabled": True,
    }
    await supabase.insert("integrations", row, upsert=True, on_conflict="user_id,provider")
    return await run_sync("powerschool", user.id)


def _schoology_api_base(body: SchoologyConnectRequest) -> str:
    return (body.api_base or SCHOOLOGY_API_BASE).strip().rstrip("/")


@router.post("/schoology/verify")
async def verify_schoology(
    body: SchoologyConnectRequest, user: CurrentUser = Depends(get_current_user)
):
    """Check a Schoology API key + secret without saving them — lets the connect
    screen confirm the credentials work (and show how many courses were found)
    before committing."""
    provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
    try:
        return await provider.verify(
            body.consumer_key.strip(), body.consumer_secret.strip(), _schoology_api_base(body)
        )
    except SchoologyAuthError as e:
        raise HTTPException(401, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not reach Schoology: {e}") from e


@router.post("/schoology/connect", status_code=201)
async def connect_schoology(
    body: SchoologyConnectRequest, user: CurrentUser = Depends(get_current_user)
):
    """Save the Schoology API key + secret (encrypted) and run a first sync so
    the student sees right away whether it works."""
    config: dict[str, str] = {"auth_mode": "api_key", "api_base": _schoology_api_base(body)}
    if body.domain:
        config["domain"] = body.domain.strip().rstrip("/")
    row = {
        "user_id": user.id,
        "provider": "schoology",
        "display_name": body.display_name or "Schoology",
        "config": config,
        "secret_ref": encrypt_api_key(body.consumer_key.strip(), body.consumer_secret.strip()),
        "enabled": True,
    }
    await supabase.insert("integrations", row, upsert=True, on_conflict="user_id,provider")
    return await run_sync("schoology", user.id)


@router.get("/powerschool/debug-scrape")
async def debug_scrape_powerschool(user: CurrentUser = Depends(get_current_user)):
    """Fetches the already-connected account's authenticated grades page and
    reports its raw table structure — a self-serve way to see why scraping
    got the wrong data for a district (e.g. extra attendance columns) without
    needing browser dev tools access."""
    provider = PROVIDERS["powerschool"]
    try:
        return await provider.debug_scrape(user.id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@router.post("/powerschool/connect-session", status_code=201)
async def connect_powerschool_session(
    body: PowerSchoolConnectSessionRequest, user: CurrentUser = Depends(get_current_user)
):
    """For SSO-gated districts (Google/Microsoft/Clever) — no login form
    exists to automate, so this saves a pasted session cookie instead and
    runs a first sync immediately."""
    base_url = _normalize_base_url(body.base_url)
    row = {
        "user_id": user.id,
        "provider": "powerschool",
        "display_name": body.display_name or "PowerSchool",
        "config": {"base_url": base_url, "auth_mode": "cookie"},
        "secret_ref": encrypt_session_cookie(body.cookie),
        "enabled": True,
    }
    await supabase.insert("integrations", row, upsert=True, on_conflict="user_id,provider")
    return await run_sync("powerschool", user.id)
