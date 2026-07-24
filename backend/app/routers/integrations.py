"""Integrations (Phase 2 scaffold) — LMS connections & sync orchestration.

The actual provider clients (Schoology, PowerSchool, Blackboard, …) live in
`app.integrations`. This router manages connection records and triggers syncs.
Automation (n8n) can also call these endpoints on a schedule.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.integrations import PROVIDERS, cancel_sync, reconcile_stale_syncs, run_sync, run_sync_for_all
from app.integrations.powerschool import encrypt_credentials, encrypt_session_cookie
from app.integrations.powerschool_client import PowerSchoolClient
from app.integrations.schoology import (
    SchoologyProvider,
    encrypt_api_key,
    merge_scraper_credentials,
)
from app.integrations.schoology_client import API_BASE as SCHOOLOGY_API_BASE
from app.integrations.schoology_client import SchoologyAuthError
from app.integrations.schoology_scraper import SchoologyScraperAuthError
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
    # Without this, a row stuck on "running" by a hard-killed sync (see
    # SYNC_TIMEOUT_SECONDS's docstring) only ever gets cleared by the next
    # scheduled cron sweep — up to 12 hours away — so just opening the page
    # kept showing a permanently-stuck sync until someone clicked Cancel.
    for provider in PROVIDERS:
        await reconcile_stale_syncs(provider)
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


@router.post("/{provider}/cancel")
async def cancel_provider_sync(provider: str, user: CurrentUser = Depends(get_current_user)):
    """Clear a "running" status stuck on this account's row so the UI (and
    the next "Sync now" click) aren't blocked on it — see `cancel_sync`'s
    docstring for what this can and can't actually stop."""
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Known: {list(PROVIDERS)}")
    return await cancel_sync(provider, user.id)


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
    """Check a Schoology API key + secret without saving them — lets the
    connect screen's optional "Advanced" section confirm the key works (and
    show how many courses were found) before committing. Only relevant when
    the caller is actually filling in the optional key; username/password
    are unused here."""
    if not body.consumer_key or not body.consumer_secret:
        raise HTTPException(400, "Enter both the consumer key and secret to test them.")
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
    """Save the Schoology login (username + password — the same credentials
    used in a browser) and run a first sync immediately, so problems surface
    right away instead of on the next scheduled run. This is the only
    connect step now — it used to require a personal API key first and a
    separate materials-access login after; the login here reads courses and
    materials on its own. An API key is optional (an "Advanced" field): when
    supplied and valid, it additionally unlocks assignments/events sync,
    which the login session alone can't read yet."""
    domain = body.domain.strip().rstrip("/")
    if not domain:
        raise HTTPException(400, "Missing Schoology web address (e.g. https://yourdistrict.schoology.com).")
    if not body.username.strip() or not body.password:
        raise HTTPException(400, "Username and password are required.")

    # Username isn't actually secret (usually just an email) — kept in
    # `config` too, not just inside the encrypted `secret_ref`, so the
    # connect form can show what's already saved instead of forcing a blank
    # re-entry of everything on every edit.
    config: dict[str, Any] = {"auth_mode": "scraper", "domain": domain, "username": body.username.strip()}
    secret_ref = merge_scraper_credentials("", body.username.strip(), body.password)

    if body.consumer_key and body.consumer_secret:
        api_base = _schoology_api_base(body)
        provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
        try:
            await provider.verify(body.consumer_key.strip(), body.consumer_secret.strip(), api_base)
        except SchoologyAuthError as e:
            raise HTTPException(401, f"API key: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Could not reach Schoology: {e}") from e
        secret_ref = merge_scraper_credentials(
            encrypt_api_key(body.consumer_key.strip(), body.consumer_secret.strip()),
            body.username.strip(), body.password,
        )
        config["auth_mode"] = "api_key+scraper"
        config["api_base"] = api_base

    row = {
        "user_id": user.id,
        "provider": "schoology",
        "display_name": body.display_name or "Schoology",
        "config": config,
        "secret_ref": secret_ref,
        "enabled": True,
    }
    await supabase.insert("integrations", row, upsert=True, on_conflict="user_id,provider")

    provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
    try:
        await provider.verify_materials_login(user.id)
    except SchoologyScraperAuthError as e:
        raise HTTPException(401, str(e)) from e

    return await run_sync("schoology", user.id)


@router.get("/schoology/debug-scrape-materials")
async def debug_scrape_materials_schoology(
    q: str | None = None, user: CurrentUser = Depends(get_current_user)
):
    """Logs in with the saved Schoology username/password and fetches one or
    more sections' materials page verbatim (title, links, HTML snippet) — a
    self-serve way to confirm the real authenticated page shape before a
    parser is written against it. `q` narrows to sections whose name contains
    it (e.g. `?q=AP+Physics`); omit it to probe the first academic section."""
    provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
    try:
        return await provider.debug_scrape_materials(user.id, query=q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@router.get("/schoology/debug-walk-materials")
async def debug_walk_materials_schoology(
    q: str | None = None, user: CurrentUser = Depends(get_current_user)
):
    """Walks one or more sections' materials folders via the scraper login
    and returns the classified result — real folders recursed into, real
    items returned, Schoology's page chrome (nav, app launchers, type
    filters, admin/export links) filtered out. A self-serve way to confirm
    the parser (`schoology_scraper.parse_materials_page`) against a real
    account before `sync()` relies on it. `q` narrows to sections whose name
    contains it (e.g. `?q=AP+Biology`); omit it to probe the first academic
    section."""
    provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
    try:
        return await provider.debug_walk_materials(user.id, query=q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@router.get("/schoology/debug-fetch")
async def debug_fetch_schoology(
    q: str | None = None, user: CurrentUser = Depends(get_current_user)
):
    """Fetches one or more connected sections' raw assignments/events/
    folder-root response from Schoology and returns it verbatim — a self-serve
    way to see whether the API key can actually read content (some districts
    issue student keys that can list sections but are denied assignments/
    materials access, which looks like a normal successful-but-empty sync)
    without needing server log access. `q` narrows to sections whose name
    contains it (e.g. `?q=AP+Physics`); omit it to just probe the first
    academic section found."""
    provider: SchoologyProvider = PROVIDERS["schoology"]  # type: ignore[assignment]
    try:
        return await provider.debug_fetch(user.id, query=q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


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
