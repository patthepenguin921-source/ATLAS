"""Async Supabase data-access layer (PostgREST + Auth + RPC over httpx).

We talk to PostgREST directly rather than pulling in the full Supabase SDK:
it keeps dependencies light, the calls fully async, and gives us precise
control over headers and error handling. The backend uses the *service role*
key (bypasses RLS) and always scopes queries to the authenticated user.

File storage lives in Cloudflare R2, not here — see `r2_client.py`.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import settings

# NUL bytes and lone (unpaired) UTF-16 surrogate codepoints. Both are valid
# in a Python str (badly-decoded PDF/OCR text and CMap-less font extraction
# routinely produce them) but Postgres `text` columns reject either with an
# "unsupported Unicode escape sequence" / "invalid byte sequence" error.
_INVALID_TEXT_RE = re.compile(r"[\x00\ud800-\udfff]")


def _strip_null_bytes(value: Any) -> Any:
    """Recursively drop chars Postgres `text` columns reject from a payload.

    Extracted document content (PDF/OCR text, etc.) is the usual source, and
    can also leak into LLM-derived fields (titles, summaries) that echo it
    back. PostgREST returns a 400 for the whole request if any string in the
    payload contains one, so we sanitize at the transport boundary rather
    than trying to catch every producer.
    """
    if isinstance(value, str):
        return _INVALID_TEXT_RE.sub("", value) if _INVALID_TEXT_RE.search(value) else value
    if isinstance(value, dict):
        return {k: _strip_null_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_null_bytes(v) for v in value]
    return value


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
            json=_strip_null_bytes(rows),
        )
        return self._parse(r)

    async def update(self, table: str, patch: dict, *, filters: dict[str, str]) -> Any:
        client = self._require()
        r = await client.patch(
            f"{self._rest}/{table}",
            params=filters,
            headers=self._headers({"Prefer": "return=representation"}),
            json=_strip_null_bytes(patch),
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
        r = await client.post(
            f"{self._rest}/rpc/{fn}", headers=self._headers(), json=_strip_null_bytes(payload)
        )
        return self._parse(r)

    # ---- auth ----
    async def get_user(self, access_token: str) -> dict:
        """Verify a Supabase-issued access token by asking Supabase Auth directly.

        This avoids reimplementing JWT verification locally (which breaks the
        moment a project uses asymmetric JWT signing keys instead of the
        legacy shared secret) — Supabase always knows how to validate its
        own tokens.
        """
        client = self._require()
        key = settings.supabase_anon_key or self._key
        r = await client.get(
            f"{self._base}/auth/v1/user",
            headers={"apikey": key, "Authorization": f"Bearer {access_token}"},
        )
        return self._parse(r)

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
