"""Base integration provider + normalization/persistence helpers."""
from __future__ import annotations

from typing import Any

from app.core.supabase_client import eq, supabase


class IntegrationProvider:
    name: str = "base"
    status: str = "stub"  # stub | beta | stable

    async def sync(self, user_id: str) -> dict[str, Any]:
        """Pull remote data and upsert into Atlas. Override per provider."""
        raise NotImplementedError(
            f"The {self.name} provider is a scaffold. Implement `sync()` with the "
            "district's API/OAuth to enable automatic import."
        )

    # ---- shared upsert helpers (normalized → Atlas schema) ----
    async def upsert_course(self, user_id: str, external_id: str, fields: dict[str, Any]) -> str:
        existing = await supabase.select(
            "courses", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(external_id),
                     "external_source": eq(self.name)}, limit=1,
        )
        payload = {**fields, "user_id": user_id, "external_id": external_id,
                   "external_source": self.name}
        if existing:
            await supabase.update("courses", payload, filters={"id": eq(existing[0]["id"])})
            return existing[0]["id"]
        created = await supabase.insert("courses", payload)
        return created[0]["id"]

    async def upsert_club(self, user_id: str, external_id: str, fields: dict[str, Any]) -> str:
        existing = await supabase.select(
            "clubs", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(external_id),
                     "external_source": eq(self.name)}, limit=1,
        )
        payload = {**fields, "user_id": user_id, "external_id": external_id,
                   "external_source": self.name}
        if existing:
            await supabase.update("clubs", payload, filters={"id": eq(existing[0]["id"])})
            return existing[0]["id"]
        created = await supabase.insert("clubs", payload)
        return created[0]["id"]

    async def upsert_assignment(self, user_id: str, external_id: str, fields: dict[str, Any]) -> str:
        existing = await supabase.select(
            "assignments", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(external_id),
                     "external_source": eq(self.name)}, limit=1,
        )
        payload = {**fields, "user_id": user_id, "external_id": external_id,
                   "external_source": self.name}
        if existing:
            await supabase.update("assignments", payload, filters={"id": eq(existing[0]["id"])})
            return existing[0]["id"]
        created = await supabase.insert("assignments", payload)
        return created[0]["id"]

    async def upsert_grade(
        self, user_id: str, assignment_id: str, course_id: str | None, fields: dict[str, Any]
    ) -> str:
        """An assignment has one authoritative grade, so this keys on assignment_id."""
        existing = await supabase.select(
            "grades", columns="id",
            filters={"user_id": eq(user_id), "assignment_id": eq(assignment_id)}, limit=1,
        )
        payload = {**fields, "user_id": user_id, "assignment_id": assignment_id, "course_id": course_id}
        if existing:
            await supabase.update("grades", payload, filters={"id": eq(existing[0]["id"])})
            return existing[0]["id"]
        created = await supabase.insert("grades", payload)
        return created[0]["id"]
