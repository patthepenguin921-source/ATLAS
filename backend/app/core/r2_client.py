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
from typing import Any
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


def _control_char_position(value: str) -> int | None:
    """Index of the first control character (the class httpx's URL parser
    rejects — a literal newline being the practical case seen in production,
    from an env var value with a trailing newline) in `value`, or None if
    it's clean."""
    for i, ch in enumerate(value):
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return i
    return None


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
            # A flat 30s timeout applies to connect/read/write/pool alike;
            # that's plenty for a small object but not always enough to
            # *write* a large one (a 240-page/6MB+ course PDF reliably hit
            # this in production) — connecting and reading a response stay
            # fast, so only the write (upload body) and read (final
            # response, in case R2 is slow to acknowledge a big object) get
            # real headroom.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, write=180.0, read=60.0))

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
            # A flat 30s timeout applies to connect/read/write/pool alike;
            # that's plenty for a small object but not always enough to
            # *write* a large one (a 240-page/6MB+ course PDF reliably hit
            # this in production) — connecting and reading a response stay
            # fast, so only the write (upload body) and read (final
            # response, in case R2 is slow to acknowledge a big object) get
            # real headroom.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, write=180.0, read=60.0))
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

    def _check_url_components(self, key: str) -> None:
        """Raise a precise, actionable error instead of httpx's generic
        "Invalid non-printable ASCII character in URL" — that message alone
        doesn't say whether the bad character is in the account id/host
        (config — e.g. a stray newline pasted into R2_ACCOUNT_ID), the
        bucket name (config), or the storage key (derived from a filename,
        already sanitized by `safe_object_name` — so this would mean that
        sanitizer has a real gap, not a config problem). Reports only
        position/length, never the value itself, since the host embeds the
        account id."""
        for label, value in (("account id", self._account_id), ("bucket", self._bucket), ("key", key)):
            pos = _control_char_position(value)
            if pos is not None:
                raise R2Error(
                    0,
                    f"R2 {label} contains a control character (e.g. a stray "
                    f"newline) at position {pos} of {len(value)} characters — "
                    + ("check the R2_ACCOUNT_ID env var for stray whitespace."
                       if label == "account id" else
                       "check the ATLAS_STORAGE_BUCKET env var for stray whitespace."
                       if label == "bucket" else
                       "this shouldn't happen after safe_object_name(); please report it."),
                )

    # ---- object operations ----
    async def upload(self, key: str, content: bytes, content_type: str) -> None:
        self._check_url_components(key)
        client = self._require()
        payload_hash = hashlib.sha256(content).hexdigest()
        headers = self._sign_request(
            "PUT", key, payload_hash=payload_hash, extra_headers={"content-type": content_type}
        )
        r = await client.put(f"https://{self._host}{self._canonical_uri(key)}", headers=headers, content=content)
        if r.status_code >= 300:
            raise R2Error(r.status_code, r.text)

    async def list_objects(
        self, *, prefix: str = "", continuation_token: str | None = None, max_keys: int = 1000,
    ) -> dict[str, Any]:
        """Raw S3 ListObjectsV2 against the bucket root. Returns
        `{"keys": [...], "next_token": str | None}` — a `next_token` means
        there are more pages; pass it back as `continuation_token` to
        continue. Nothing in the app's normal upload/download path needs to
        enumerate the bucket — this exists for one-off admin tooling (see
        `scripts/cleanup_orphaned_r2_objects.py`), signed separately from
        `_sign_request` since that one only ever signs a per-object
        (no-query-string) request."""
        client = self._require()
        query: dict[str, str] = {"list-type": "2", "max-keys": str(max_keys)}
        if prefix:
            query["prefix"] = prefix
        if continuation_token:
            query["continuation-token"] = continuation_token
        canonical_query = "&".join(f"{_quote_query(k)}={_quote_query(query[k])}" for k in sorted(query))

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(b"").hexdigest()
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_uri = f"/{self._bucket}"
        canonical_request = "\n".join([
            "GET", canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash,
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

        # Built and sent as one already-encoded URL (not httpx's `params=`)
        # so what's sent is byte-for-byte what was just signed — a second,
        # independent re-encoding pass risks disagreeing on the exact
        # percent-encoding of a continuation token and failing signature
        # verification.
        r = await client.get(f"https://{self._host}{canonical_uri}?{canonical_query}", headers=request_headers)
        if r.status_code >= 300:
            raise R2Error(r.status_code, r.text)
        keys = re.findall(r"<Key>(.*?)</Key>", r.text)
        next_token = None
        if "<IsTruncated>true</IsTruncated>" in r.text:
            m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", r.text)
            next_token = m.group(1) if m else None
        return {"keys": keys, "next_token": next_token}

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
