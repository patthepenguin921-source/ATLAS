"""Async Supabase data-access layer (PostgREST + Storage + RPC over httpx).

We talk to PostgREST directly rather than pulling in the full Supabase SDK:
it keeps dependencies light, the calls fully async, and gives us precise
control over headers and error handling. The backend uses the *service role*
key (bypasses RLS) and always scopes queries to the authenticated user.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class SupabaseError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"Supabase error {status}: {detail}")
        self.status = status
        self.detail = detail


class SupabaseClient:
    def __init__(self) -> None:
        self._base = settings.supabase_url.rstrip("/")
        self._key = settings.supabase_service_role_key
        self._rest = f"{self._base}/rest/v1"
        self._storage = f"{self._base}/storage/v1"
        self._client: httpx.AsyncClient | None = None

    # ---- lifecycle ----
    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def enabled(self) -> bool:
        return settings.has_supabase

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _require(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise SupabaseError(503, "Supabase is not configured (set SUPABASE_URL / service role key).")
        if self._client is None:  # lazily start if used outside lifespan
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    # ---- table operations ----
    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
        single: bool = False,
    ) -> Any:
        client = self._require()
        params: dict[str, str] = {"select": columns}
        if filters:
            params.update(filters)
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        headers = self._headers()
        if single:
            headers["Accept"] = "application/vnd.pgrst.object+json"
        r = await client.get(f"{self._rest}/{table}", params=params, headers=headers)
        return self._parse(r)

    async def insert(self, table: str, rows: dict | list[dict], *, upsert: bool = False) -> Any:
        client = self._require()
        prefer = "return=representation"
        if upsert:
            prefer += ",resolution=merge-duplicates"
        r = await client.post(
            f"{self._rest}/{table}",
            headers=self._headers({"Prefer": prefer}),
            json=rows,
        )
        return self._parse(r)

    async def update(self, table: str, patch: dict, *, filters: dict[str, str]) -> Any:
        client = self._require()
        r = await client.patch(
            f"{self._rest}/{table}",
            params=filters,
            headers=self._headers({"Prefer": "return=representation"}),
            json=patch,
        )
        return self._parse(r)

    async def delete(self, table: str, *, filters: dict[str, str]) -> Any:
        client = self._require()
        r = await client.delete(
            f"{self._rest}/{table}",
            params=filters,
            headers=self._headers({"Prefer": "return=representation"}),
        )
        return self._parse(r)

    async def rpc(self, fn: str, payload: dict[str, Any]) -> Any:
        client = self._require()
        r = await client.post(f"{self._rest}/rpc/{fn}", headers=self._headers(), json=payload)
        return self._parse(r)

    # ---- storage ----
    async def upload(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        client = self._require()
        r = await client.post(
            f"{self._storage}/object/{bucket}/{path}",
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            content=content,
        )
        if r.status_code >= 300:
            raise SupabaseError(r.status_code, r.text)

    async def download(self, bucket: str, path: str) -> bytes:
        client = self._require()
        r = await client.get(
            f"{self._storage}/object/{bucket}/{path}",
            headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
        )
        if r.status_code >= 300:
            raise SupabaseError(r.status_code, r.text)
        return r.content

    async def signed_url(self, bucket: str, path: str, expires_in: int = 3600) -> str:
        client = self._require()
        r = await client.post(
            f"{self._storage}/object/sign/{bucket}/{path}",
            headers=self._headers(),
            json={"expiresIn": expires_in},
        )
        data = self._parse(r)
        return f"{self._base}/storage/v1{data['signedURL']}"

    # ---- helpers ----
    @staticmethod
    def _parse(r: httpx.Response) -> Any:
        if r.status_code >= 300:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise SupabaseError(r.status_code, detail)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()


# Singleton used across the app
supabase = SupabaseClient()


def eq(value: str) -> str:
    """PostgREST equality filter value helper."""
    return f"eq.{value}"
