"""Async Cloudflare R2 (S3-compatible) storage client.

R2's S3 API needs AWS SigV4-signed requests but no SDK. We hand-roll the
signing over httpx rather than pulling in boto3, for the same reason
`supabase_client.py` skips the Supabase SDK: it keeps the client lightweight
and fully async. R2's free tier (10 GB, zero egress) replaces Supabase
Storage (1 GB) as the home for original uploaded documents; Postgres/auth
stay on Supabase.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.config import settings

_SERVICE = "s3"
_REGION = "auto"

# R2/S3 object keys only accept a restricted, mostly-ASCII charset. Filenames
# with em/en dashes, curly quotes, accented letters, emoji, etc. make an
# upload fail — replace anything outside that charset instead of losing the
# file. Shared by every caller that builds a storage key from a filename
# (direct upload, Drive import, Schoology material sync, …).
_UNSAFE_KEY_CHARS = re.compile(r"[^\w!\-.*'() &$@=;:+,?]")


def safe_object_name(filename: str) -> str:
    name = (filename or "document").replace("/", "_")
    return _UNSAFE_KEY_CHARS.sub("_", name) or "document"


class R2Error(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"R2 storage error {status}: {detail}")
        self.status = status
        self.detail = detail


def _quote_path(s: str) -> str:
    return quote(s, safe="/-_.~")


def _quote_query(s: str) -> str:
    return quote(s, safe="-_.~")


def _signing_key(secret: str, date_stamp: str) -> bytes:
    k_date = hmac.new(f"AWS4{secret}".encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, _REGION.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, _SERVICE.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


class R2Client:
    def __init__(self) -> None:
        self._account_id = settings.r2_account_id
        self._access_key = settings.r2_access_key_id
        self._secret_key = settings.r2_secret_access_key
        self._bucket = settings.atlas_storage_bucket
        self._host = f"{self._account_id}.r2.cloudflarestorage.com"
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
        return settings.has_r2

    def _require(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise R2Error(503, "R2 is not configured (set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY).")
        if self._client is None:  # lazily start if used outside lifespan
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _canonical_uri(self, key: str) -> str:
        return f"/{self._bucket}/{_quote_path(key)}"

    def _sign_request(
        self, method: str, key: str, *, payload_hash: str, extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        headers.update({k.lower(): v for k, v in (extra_headers or {}).items()})
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = "\n".join([
            method, self._canonical_uri(key), "", canonical_headers, signed_headers, payload_hash,
        ])
        credential_scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        signature = hmac.new(
            _signing_key(self._secret_key, date_stamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        auth = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        request_headers = {k: v for k, v in headers.items() if k != "host"}
        request_headers["Authorization"] = auth
        return request_headers

    # ---- object operations ----
    async def upload(self, key: str, content: bytes, content_type: str) -> None:
        client = self._require()
        payload_hash = hashlib.sha256(content).hexdigest()
        headers = self._sign_request(
            "PUT", key, payload_hash=payload_hash, extra_headers={"content-type": content_type}
        )
        r = await client.put(f"https://{self._host}{self._canonical_uri(key)}", headers=headers, content=content)
        if r.status_code >= 300:
            raise R2Error(r.status_code, r.text)

    async def remove(self, key: str) -> None:
        client = self._require()
        payload_hash = hashlib.sha256(b"").hexdigest()
        headers = self._sign_request("DELETE", key, payload_hash=payload_hash)
        r = await client.delete(f"https://{self._host}{self._canonical_uri(key)}", headers=headers)
        # R2 returns 204 whether or not the key existed; only surface real failures.
        if r.status_code >= 300 and r.status_code != 404:
            raise R2Error(r.status_code, r.text)

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Presigned GET URL. Pure SigV4 query signing — no request is made."""
        if not self.enabled:
            raise R2Error(503, "R2 is not configured (set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY).")
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        credential_scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self._access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(f"{_quote_query(k)}={_quote_query(v)}" for k, v in sorted(query.items()))
        canonical_request = "\n".join([
            "GET", self._canonical_uri(key), canonical_query, f"host:{self._host}\n", "host", "UNSIGNED-PAYLOAD",
        ])
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        signature = hmac.new(
            _signing_key(self._secret_key, date_stamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        return f"https://{self._host}{self._canonical_uri(key)}?{canonical_query}&X-Amz-Signature={signature}"


# Singleton used across the app
r2 = R2Client()
