"""PowerSchool provider — logs into the Guardian/Student portal and imports
courses, current grades, and per-assignment scores.

See `powerschool_client.py` for the login/scraping mechanics and its caveats.
Credentials are stored encrypted in `integrations.secret_ref` (see
`app.core.crypto`) since there's no OAuth token to hold onto instead.
"""
from __future__ import annotations

from typing import Any

from app.core.crypto import decrypt_json, encrypt_json
from app.core.supabase_client import eq, supabase
from app.integrations.base import IntegrationProvider
from app.integrations.powerschool_client import (
    PowerSchoolAuthError,
    PowerSchoolClient,
    map_category,
    map_status,
)


def encrypt_credentials(username: str, password: str) -> str:
    return encrypt_json({"username": username, "password": password})


class PowerSchoolProvider(IntegrationProvider):
    name = "powerschool"
    status = "beta"

    async def sync(self, user_id: str) -> dict[str, Any]:
        rows = await supabase.select(
            "integrations", filters={"user_id": eq(user_id), "provider": eq(self.name)}, limit=1,
        )
        if not rows or not rows[0].get("secret_ref"):
            raise RuntimeError(
                "PowerSchool isn't connected yet — add your portal URL and login first."
            )
        integration = rows[0]
        base_url = (integration.get("config") or {}).get("base_url")
        if not base_url:
            raise RuntimeError("PowerSchool integration is missing its portal URL.")
        creds = decrypt_json(integration["secret_ref"])

        client = PowerSchoolClient(base_url, creds["username"], creds["password"])
        try:
            try:
                await client.login()
            except PowerSchoolAuthError as e:
                raise RuntimeError(str(e)) from e

            classes = await client.fetch_classes()
            courses = assignments_count = grades_count = 0
            errors: list[str] = []

            for cls in classes:
                course_id = await self.upsert_course(user_id, cls.ccid, {
                    "name": cls.name,
                    "period": cls.period,
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
