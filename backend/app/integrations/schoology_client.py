"""Schoology REST API v1 client (two-legged OAuth 1.0a).

Unlike PowerSchool, Schoology has a real, documented REST API. A student
generates a personal API key + secret at ``<their-domain>/api`` (issued
instantly by Schoology itself, not the district) and Atlas signs every request
with **two-legged** OAuth 1.0a — i.e. there's no per-user access token to
exchange for; the consumer key/secret alone authenticate as that user, at that
user's own permission level. See https://developers.schoology.com/api/ .

The REST host is always ``https://api.schoology.com/v1`` regardless of the
district's web subdomain (e.g. lexington1.schoology.com) — the subdomain only
serves the web UI and the ``/api`` key-generation page.

Signing notes (learned against the live API):
  * The signature base string MUST include any query-string parameters, sorted
    alongside the ``oauth_*`` params — omitting them yields a 401 "signature
    failed". ``_auth_header`` folds the URL's query into the base string.
  * HMAC-SHA1 with signing key ``"<secret>&"`` (empty token secret) works;
    the ``Authorization: OAuth`` header carries the realm + oauth params.
  * ``GET /users/me`` 302-redirects to ``/users/{uid}`` and re-sends the same
    signed header, which Schoology rejects as a replay — so resolve the current
    user id via ``GET /app-user-info`` (returns ``api_uid``) instead.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

API_BASE = "https://api.schoology.com/v1"
_REALM = "Schoology API"
# Guard rails so a pathological/looping folder tree or a huge course can't make
# a single sync run unbounded.
_MAX_FOLDER_DEPTH = 8
_PAGE_LIMIT = 200
_MAX_PAGES = 25


class SchoologyAuthError(RuntimeError):
    """Raised when Schoology rejects the API key/secret (401/403)."""


def _q(value: Any) -> str:
    """OAuth percent-encoding (RFC 5849 §3.6: unreserved chars + ``~``)."""
    return quote(str(value), safe="~")


@dataclass
class SchoologySection:
    id: str
    course_title: str
    section_title: str
    course_code: str
    section_code: str
    grading_periods: list[int]
    meeting_days: list[str]
    start_time: str
    end_time: str
    location: str
    active: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        title = self.course_title.strip() or self.section_title.strip()
        return title or f"Section {self.id}"


@dataclass
class SchoologyAssignment:
    id: str
    title: str
    description: str
    due: str | None
    max_points: float | None
    assignment_type: str
    web_url: str
    folder_id: str | None
    published: bool
    attachments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchoologyEvent:
    id: str
    title: str
    description: str
    start: str | None
    end: str | None
    all_day: bool
    type: str
    assignment_id: str | None
    web_url: str


@dataclass
class SchoologyMaterial:
    """A single item found while walking a section's folder tree — a document,
    page, discussion, assignment, etc. — carrying whatever attachments it
    exposes (files + links)."""

    id: str
    type: str
    title: str
    body: str
    url: str | None            # external URL for link-type items
    location: str | None       # API path to fetch full details/attachments
    folder_path: str           # human-readable breadcrumb, e.g. "Unit 1/Notes"
    attachments: dict[str, Any] = field(default_factory=dict)


def _as_list(container: Any, key: str) -> list[dict[str, Any]]:
    """Schoology wraps collections as ``{"<key>": [...]}`` but collapses a
    single item to a dict and omits the key entirely when empty."""
    if not isinstance(container, dict):
        return []
    value = container.get(key)
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any, default: bool) -> bool:
    """Schoology's 0/1 boolean flags arrive as JSON integers. `value or
    "<default>"` (the previous approach) silently loses a real ``0`` — the
    int ``0`` is falsy, so it falls through to the default instead of being
    read as False. Missing/blank stays default; everything else is judged on
    its own value."""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() not in ("0", "false")


def _parse_dt(value: str | None) -> str | None:
    """Schoology datetimes look like ``2022-12-06 23:59:00`` (or a bare date).
    Return an ISO-8601 string, or ``None`` for empty/unparseable values."""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return None


class SchoologyClient:
    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        *,
        api_base: str = API_BASE,
        transport: httpx.BaseTransport | None = None,
    ):
        self._key = consumer_key
        self._secret = consumer_secret
        self._base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=30.0,
            transport=transport,
            headers={"User-Agent": "AtlasAcademicAssistant/1.0"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- OAuth 1.0a two-legged signing ----
    def _auth_header(self, method: str, url: str) -> str:
        parts = urlsplit(url)
        base_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        query = parse_qsl(parts.query, keep_blank_values=True)
        oauth = {
            "oauth_consumer_key": self._key,
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_timestamp": str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version": "1.0",
        }
        # The base string must include the query params, sorted together with
        # the oauth params — otherwise Schoology returns "signature failed".
        all_params = sorted(
            [(k, str(v)) for k, v in oauth.items()]
            + [(k, str(v)) for k, v in query]
        )
        param_str = "&".join(f"{_q(k)}={_q(v)}" for k, v in all_params)
        base_string = "&".join([method.upper(), _q(base_url), _q(param_str)])
        signature = base64.b64encode(
            hmac.new(f"{self._secret}&".encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()
        oauth["oauth_signature"] = signature
        pairs = ",".join(f'{_q(k)}="{_q(v)}"' for k, v in oauth.items())
        return f'OAuth realm="{_REALM}",{pairs}'

    async def _get(self, path: str) -> httpx.Response:
        url = path if path.startswith("http") else f"{self._base}/{path.lstrip('/')}"
        # Do NOT follow redirects: Schoology 302s re-send the one-time-signed
        # header and get rejected as a replay. Endpoints we use resolve directly.
        r = await self._client.get(
            url,
            headers={"Authorization": self._auth_header("GET", url), "Accept": "application/json"},
            follow_redirects=False,
        )
        if r.status_code in (401, 403):
            raise SchoologyAuthError(
                "Schoology rejected the API key/secret (check that they're correct and that your "
                f"district allows API access): {r.text[:200]}"
            )
        return r

    async def _get_json(self, path: str) -> dict[str, Any]:
        r = await self._get(path)
        if r.status_code >= 300:
            raise RuntimeError(f"Schoology API error {r.status_code} for {path}: {r.text[:200]}")
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    async def _paged(self, path: str, key: str) -> list[dict[str, Any]]:
        """Fetch a collection, following ``links.next`` pagination up to a cap."""
        sep = "&" if "?" in path else "?"
        next_url: str | None = f"{path}{sep}limit={_PAGE_LIMIT}"
        items: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            if not next_url:
                break
            data = await self._get_json(next_url)
            items.extend(_as_list(data, key))
            next_url = (data.get("links") or {}).get("next")
        return items

    # ---- identity ----
    async def current_user_id(self) -> str:
        data = await self._get_json("/app-user-info")
        uid = data.get("api_uid") or data.get("uid") or data.get("id")
        if not uid:
            raise SchoologyAuthError("Could not resolve the Schoology user id for these credentials.")
        return str(uid)

    async def verify(self) -> dict[str, Any]:
        """Confirm the credentials work and report basic account info — used by
        the connect flow to fail fast on a bad key/secret."""
        uid = await self.current_user_id()
        sections = await self.get_sections(uid)
        return {"api_uid": uid, "section_count": len(sections)}

    # ---- courses / sections ----
    async def get_sections(self, uid: str) -> list[SchoologySection]:
        raw = await self._paged(f"/users/{uid}/sections", "section")
        sections: list[SchoologySection] = []
        for s in raw:
            sections.append(SchoologySection(
                id=str(s.get("id")),
                course_title=str(s.get("course_title") or ""),
                section_title=str(s.get("section_title") or ""),
                course_code=str(s.get("course_code") or ""),
                section_code=str(s.get("section_code") or ""),
                grading_periods=[int(g) for g in (s.get("grading_periods") or []) if str(g).isdigit()],
                meeting_days=[str(d) for d in (s.get("meeting_days") or []) if str(d).strip()],
                start_time=str(s.get("start_time") or ""),
                end_time=str(s.get("end_time") or ""),
                location=str(s.get("location") or ""),
                active=_to_bool(s.get("active"), default=True),
                raw=s,
            ))
        return sections

    # ---- assignments ----
    async def get_assignments(self, section_id: str) -> list[SchoologyAssignment]:
        raw = await self._paged(
            f"/sections/{section_id}/assignments?with_attachments=1", "assignment"
        )
        out: list[SchoologyAssignment] = []
        for a in raw:
            out.append(SchoologyAssignment(
                id=str(a.get("id")),
                title=str(a.get("title") or "Untitled"),
                description=str(a.get("description") or ""),
                due=_parse_dt(a.get("due")),
                max_points=_to_float(a.get("max_points")),
                assignment_type=str(a.get("assignment_type") or a.get("type") or "assignment"),
                web_url=str(a.get("web_url") or ""),
                folder_id=str(a.get("folder_id")) if a.get("folder_id") else None,
                published=_to_bool(a.get("published"), default=True),
                attachments=a.get("attachments") or {},
                raw=a,
            ))
        return out

    # ---- events (week-at-a-glance) ----
    async def get_events(self, section_id: str) -> list[SchoologyEvent]:
        raw = await self._paged(f"/sections/{section_id}/events", "event")
        out: list[SchoologyEvent] = []
        for e in raw:
            out.append(SchoologyEvent(
                id=str(e.get("id")),
                title=str(e.get("title") or "Event"),
                description=str(e.get("description") or ""),
                start=_parse_dt(e.get("start")),
                end=_parse_dt(e.get("end")),
                all_day=_to_bool(e.get("all_day"), default=False),
                type=str(e.get("type") or "event"),
                assignment_id=str(e.get("assignment_id")) if e.get("assignment_id") else None,
                web_url=str(e.get("web_url") or ""),
            ))
        return out

    # ---- folders / materials (recursive) ----
    async def walk_materials(self, section_id: str) -> list[SchoologyMaterial]:
        """Depth-first walk of every folder in a section, returning every
        non-folder material found (documents, pages, discussions, assignments,
        media, links, …) with its attachments and a breadcrumb path.

        Fulfils the "look in every folder for new items" requirement.
        """
        materials: list[SchoologyMaterial] = []
        seen: set[str] = set()

        async def _walk(folder_id: str, path: str, depth: int) -> None:
            if depth > _MAX_FOLDER_DEPTH or folder_id in seen:
                return
            seen.add(folder_id)
            data = await self._get_json(f"/sections/{section_id}/folder/{folder_id}")
            for item in _as_list(data, "folder-item"):
                itype = str(item.get("type") or "").lower()
                title = str(item.get("title") or "").strip()
                item_id = str(item.get("id") or "")
                if itype == "folder":
                    child_path = f"{path}/{title}" if path and title else (title or path)
                    await _walk(item_id, child_path, depth + 1)
                    continue
                location = str(item["location"]) if item.get("location") else None
                inline_attachments = item.get("attachments") or {}
                url = str(item["url"]) if item.get("url") else None
                if not location and not url and not inline_attachments and item_id:
                    # A plain file/link dropped straight into a folder shows up
                    # in the listing with no "location", "url", or
                    # "attachments" at all (only a bare id/title) — Schoology
                    # only exposes its download info via the Documents
                    # resource. Without this fallback these items are silently
                    # dropped, which is exactly what "folder contents don't
                    # get pulled" looks like from the student's side.
                    location = f"/sections/{section_id}/documents/{item_id}"
                materials.append(SchoologyMaterial(
                    id=item_id,
                    type=itype or "document",
                    title=title or "Untitled",
                    body=str(item.get("body") or ""),
                    url=url,
                    location=location,
                    folder_path=path,
                    attachments=inline_attachments,
                ))

        await _walk("0", "", 0)
        return materials

    async def fetch_material_detail(self, material: SchoologyMaterial) -> dict[str, Any]:
        """Fetch a material's full record (with attachments) from its API
        ``location`` — folder listings often omit attachments until fetched
        directly with ``with_attachments=1``."""
        if not material.location:
            return {}
        loc = material.location
        loc += ("&" if "?" in loc else "?") + "with_attachments=1"
        return await self._get_json(loc)

    async def download_file(self, download_path: str) -> bytes:
        """Download a Schoology-hosted file attachment (its ``download_path``)
        as raw bytes, signed like every other API call."""
        r = await self._get(download_path)
        if r.status_code >= 300:
            raise RuntimeError(f"File download failed ({r.status_code}): {download_path}")
        return r.content


def files_of(attachments: dict[str, Any]) -> list[dict[str, Any]]:
    return _as_list((attachments or {}).get("files") or {}, "file")


def links_of(attachments: dict[str, Any]) -> list[dict[str, Any]]:
    return _as_list((attachments or {}).get("links") or {}, "link")


def week_bounds(today: date | None = None) -> tuple[date, date]:
    """Monday…Sunday bounds of the current week, for the week-at-a-glance filter."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)
