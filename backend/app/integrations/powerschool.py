"""PowerSchool provider — logs into the Guardian/Student portal and imports
courses, current grades, and per-assignment scores.

See `powerschool_client.py` for the login/scraping mechanics and its caveats.
Credentials are stored encrypted in `integrations.secret_ref` (see
`app.core.crypto`) since there's no OAuth token to hold onto instead.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.supabase_client import eq, supabase
from app.integrations.base import IntegrationProvider
from app.integrations.powerschool_browser import BrowserLoginError, login_and_get_cookie_header
from app.integrations.powerschool_client import (
    PowerSchoolAuthError,
    PowerSchoolClient,
    UnsupportedLoginFlow,
    map_category,
    map_status,
)


def encrypt_credentials(username: str, password: str) -> str:
    return encrypt_json({"username": username, "password": password})


def encrypt_session_cookie(cookie: str) -> str:
    return encrypt_json({"cookie": cookie.strip()})


class PowerSchoolProvider(IntegrationProvider):
    name = "powerschool"
    status = "beta"

    async def _authenticated_client(self, user_id: str) -> PowerSchoolClient:
        """Loads this user's saved PowerSchool integration and returns a
        client already past login — shared by `sync()` and the debug-scrape
        diagnostic so both use the exact same auth path (and its CAS/
        serverless fallback handling)."""
        rows = await supabase.select(
            "integrations", filters={"user_id": eq(user_id), "provider": eq(self.name)}, limit=1,
        )
        if not rows or not rows[0].get("secret_ref"):
            raise RuntimeError(
                "PowerSchool isn't connected yet — add your portal URL and login first."
            )
        integration = rows[0]
        config = integration.get("config") or {}
        base_url = config.get("base_url")
        auth_mode = config.get("auth_mode", "password")
        if not base_url:
            raise RuntimeError("PowerSchool integration is missing its portal URL.")
        creds = decrypt_json(integration["secret_ref"])

        if auth_mode == "cookie":
            client = PowerSchoolClient(base_url, session_cookie=creds["cookie"])
            try:
                await client.verify_session()
            except PowerSchoolAuthError as e:
                await client.aclose()
                raise RuntimeError(str(e)) from e
            return client

        client = PowerSchoolClient(base_url, creds["username"], creds["password"])
        try:
            await client.login()
        except UnsupportedLoginFlow:
            # Lightweight HTTP client can't speak this district's login
            # flow (e.g. a newer CAS-based one) — fall back to driving a
            # real browser, which can execute the page's JS/bot-check.
            # Not guaranteed: bot-mitigation often also weighs the
            # request's origin, and Atlas's server is cloud/datacenter
            # infrastructure regardless of using a real browser.
            await client.aclose()
            if settings.is_serverless:
                # Playwright needs a Chromium binary this platform doesn't
                # ship and enough execution time to launch/drive a
                # browser — neither holds on Vercel's serverless
                # functions. Attempting it here would just hang until the
                # platform kills the function, which surfaces to the
                # browser as an opaque "Failed to fetch" instead of a
                # real error, so fail fast with an actionable message.
                raise RuntimeError(
                    "This district's PowerSchool login uses a newer ticket-based (CAS) "
                    "flow that needs real-browser automation, which isn't available in "
                    "Atlas's hosted environment. Use Session cookie mode instead — log "
                    "into PowerSchool in your own browser and paste the session cookie."
                )
            try:
                cookie_header = await login_and_get_cookie_header(
                    base_url, creds["username"], creds["password"]
                )
            except BrowserLoginError as e:
                raise RuntimeError(
                    f"Automated login isn't working for this district: {e}"
                ) from e
            client = PowerSchoolClient(base_url, session_cookie=cookie_header)
        except PowerSchoolAuthError as e:
            await client.aclose()
            raise RuntimeError(str(e)) from e
        return client

    async def _resolve_teacher_id(self, user_id: str, name: str) -> str | None:
        """Look up (or create) a `teachers` row by name so PowerSchool-synced
        courses link `teacher_id` instead of only stashing the name in
        metadata — matches how the course detail page's teacher picker
        expects teachers to be represented."""
        name = (name or "").strip()
        if not name:
            return None
        existing = await supabase.select(
            "teachers", columns="id",
            filters={"user_id": eq(user_id), "name": eq(name)}, limit=1,
        )
        if existing:
            return existing[0]["id"]
        created = await supabase.insert("teachers", {"user_id": user_id, "name": name})
        return created[0]["id"]

    async def debug_scrape(self, user_id: str) -> dict[str, Any]:
        """Fetches the authenticated grades page and reports its raw table
        structure — lets a district's actual column layout be inspected
        (e.g. extra attendance columns shifting where the course name
        lives) without the user needing browser dev tools access."""
        client = await self._authenticated_client(user_id)
        try:
            return await client.debug_home_page()
        finally:
            await client.aclose()

    async def sync(self, user_id: str) -> dict[str, Any]:
        client = await self._authenticated_client(user_id)
        try:
            classes = await client.fetch_classes()
            courses = assignments_count = grades_count = 0
            errors: list[str] = []

            for cls in classes:
                teacher_id = await self._resolve_teacher_id(user_id, cls.teacher)
                course_id = await self.upsert_course(user_id, cls.ccid, {
                    "name": cls.name,
                    "period": cls.period,
                    "room": cls.room or None,
                    "teacher_id": teacher_id,
                    "current_grade": cls.grade_percent,
                    "current_letter": cls.grade_letter,
                    "metadata": {"teacher": cls.teacher},
                })
                courses += 1

                if not cls.detail_href:
                    continue
                try:
                    assignments = await client.fetch_assignments(cls.detail_href)
                except Exception as e:  # noqa: BLE001 — one course's markup shouldn't sink the sync
                    errors.append(f"{cls.name}: {e}")
                    continue

                for a in assignments:
                    # PowerSchool assignment rows don't expose a stable id via
                    # scraping, so the composite key keeps repeat syncs idempotent.
                    external_id = f"{cls.ccid}:{a.name}:{a.due_date or ''}"
                    assignment_id = await self.upsert_assignment(user_id, external_id, {
                        "course_id": course_id,
                        "title": a.name,
                        "category": map_category(a.category),
                        "due_date": a.due_date,
                        "points_possible": a.points_possible,
                        "status": map_status(a),
                    })
                    assignments_count += 1

                    if a.score is not None or a.percentage is not None:
                        await self.upsert_grade(user_id, assignment_id, course_id, {
                            "score": a.score,
                            "points_possible": a.points_possible,
                            "percentage": a.percentage,
                        })
                        grades_count += 1

            return {
                "courses": courses, "assignments": assignments_count,
                "grades": grades_count, "errors": errors,
            }
        finally:
            await client.aclose()
