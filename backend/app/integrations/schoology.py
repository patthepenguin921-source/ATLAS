"""Schoology provider — imports courses, a week-at-a-glance schedule, and the
full contents of every course folder (files/slideshows, links, pages) into
Atlas knowledge.

Auth is the student's own Schoology username/password (``schoology_scraper.py``
logs in the same way a browser does) — the only required credential, and the
only one materials sync ever uses. A personal API key + secret (two-legged
OAuth 1.0a, generated at ``<their-domain>/api`` — see ``schoology_client.py``)
is optional; when present it additionally unlocks assignments/events sync,
which the login session can't read yet (no scraper for those). Materials
deliberately never go through the API even when a key is on file: many
districts restrict a personal key to the Sections realm only and reject
Courses-realm access outright (confirmed via ``debug_fetch`` — even a bare
``GET /courses/{id}`` 401/403s for such a key), which used to surface as a
noisy per-course "API key rejected" error on every sync instead of a clean
empty list. Both credential sets live on the same ``integrations`` row; see
``merge_scraper_credentials``.

Deliberately does NOT touch grades: grading is owned by PowerSchool. This
provider matches each Schoology section to the student's existing (PowerSchool/
manual) course so the two systems share one course row instead of duplicating.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date, datetime
from typing import Any

from app.agents.archivist import Archivist
from app.config import settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.r2_client import r2, safe_object_name
from app.core.supabase_client import eq, supabase
from app.integrations import course_mapping
from app.integrations.base import IntegrationProvider
from app.integrations.google_files import (
    download_google_file,
    is_google_url,
    parse_google_url,
)
from app.integrations.schoology_client import (
    SchoologyAssignment,
    SchoologyClient,
    SchoologyEvent,
    SchoologySection,
    files_of,
    links_of,
    week_bounds,
)
from app.integrations.schoology_scraper import (
    MaterialLink,
    SchoologyScraperAuthError,
    SchoologyScraperClient,
)
from app.services import ingestion

# Assignment/material title keywords → Atlas assignment_category enum values.
_CATEGORY_KEYWORDS = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation",
)

# Each course's assignments/events/materials walk is independent I/O, so
# they run several at once instead of one full course after another — with
# only one course in flight at a time, a many-course account's total sync
# time is roughly (course count × per-course latency), which is what's been
# pushing accounts past the request timeout even after the shared-login fix.
# Capped (rather than unbounded) so as not to hammer Schoology or the shared
# scraper/API sessions with every course's requests at once.
_SECTION_SYNC_CONCURRENCY = 4


async def _gather_bounded(coros: list[Any], limit: int) -> None:
    """Run coroutines concurrently, at most `limit` in flight at a time."""
    semaphore = asyncio.Semaphore(limit)

    async def _run(coro: Any) -> None:
        async with semaphore:
            await coro

    await asyncio.gather(*(_run(c) for c in coros))


def encrypt_api_key(consumer_key: str, consumer_secret: str) -> str:
    return encrypt_json({"consumer_key": consumer_key, "consumer_secret": consumer_secret})


def merge_scraper_credentials(existing_secret_ref: str, username: str, password: str) -> str:
    """Add (or replace) materials-scraper login credentials on top of an
    existing encrypted secret blob, keeping whatever's already there (the
    API key) intact — the two auth methods live side by side on one
    `integrations` row: the API key still handles assignments/events, the
    scraper login is only for the materials a district's API key can't see."""
    creds = decrypt_json(existing_secret_ref) if existing_secret_ref else {}
    creds["schoology_username"] = username
    creds["schoology_password"] = password
    return encrypt_json(creds)


def _map_category(text: str) -> str:
    low = (text or "").lower()
    for key in _CATEGORY_KEYWORDS:
        if key in low:
            return key
    return "other"


def _normalize_name(name: str) -> str:
    """Lowercase alphanumeric tokens for cross-system course matching."""
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


# Course-name tokens that carry no discriminating signal when matching a
# Schoology section to a PowerSchool course (both label the same class).
_STOPWORDS = {"the", "of", "and", "a", "an", "period", "sec", "section"}


def _name_tokens(name: str) -> set[str]:
    return {t for t in _normalize_name(name).split() if t and t not in _STOPWORDS}


def _names_match(a: str, b: str) -> bool:
    """True if two course names very likely refer to the same class. Uses
    exact normalized equality, or one token set being a subset of the other
    (so "AP Biology" matches "AP Biology - Sec 1"). Deliberately conservative:
    it will NOT merge "AP Calculus AB" with "AP Calculus BC" (a differing
    token blocks the match) — a missed link just leaves a separate course,
    whereas a wrong merge corrupts two classes' data."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if na and na == nb:
        return True
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


class SchoologyProvider(IntegrationProvider):
    name = "schoology"
    status = "beta"

    # ---- auth / client ----
    async def _load_integration(self, user_id: str) -> dict[str, Any]:
        rows = await supabase.select(
            "integrations", filters={"user_id": eq(user_id), "provider": eq(self.name)}, limit=1,
        )
        if not rows or not rows[0].get("secret_ref"):
            raise RuntimeError(
                "Schoology isn't connected yet — add your Schoology login first."
            )
        return rows[0]

    def _has_api_key(self, integration: dict[str, Any]) -> bool:
        """Whether an optional personal API key was saved alongside the
        (always-required) login — see `SchoologyConnectRequest`'s docstring.
        Most accounts won't have one; the login alone drives courses and
        materials, this only gates the extra assignments/events sync."""
        creds = decrypt_json(integration["secret_ref"]) if integration.get("secret_ref") else {}
        return bool(creds.get("consumer_key") and creds.get("consumer_secret"))

    async def _client(self, integration: dict[str, Any]) -> SchoologyClient:
        creds = decrypt_json(integration["secret_ref"]) if integration.get("secret_ref") else {}
        if not creds.get("consumer_key") or not creds.get("consumer_secret"):
            raise RuntimeError(
                "No Schoology API key on file — add one under \"Advanced\" when connecting "
                "to enable assignments/events sync."
            )
        config = integration.get("config") or {}
        api_base = config.get("api_base") or "https://api.schoology.com/v1"
        return SchoologyClient(
            creds["consumer_key"], creds["consumer_secret"], api_base=api_base
        )

    async def verify(self, consumer_key: str, consumer_secret: str, api_base: str) -> dict[str, Any]:
        """Confirm a key/secret works before saving — used by the connect flow."""
        client = SchoologyClient(consumer_key, consumer_secret, api_base=api_base)
        try:
            return await client.verify()
        finally:
            await client.aclose()

    async def debug_fetch(self, user_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetch one or more connected academic sections' raw responses from
        Schoology, verbatim, without server log access — including every
        candidate shape for "the folder contents" tried so far, since which
        one this district's API key can actually use hasn't been nailed down
        by guessing alone (the courses-realm endpoint the docs describe
        rejects this key with 401/403, while the sections-realm endpoint
        returns 200 but the section's own detail object instead of a real
        folder listing). Each candidate is fetched independently and its
        error (if any) reported inline rather than aborting the whole probe,
        so a single blocked endpoint doesn't hide the others' results.
        `raw_assignments`/`raw_events` legitimately can be empty (a teacher
        who never creates graded Assignment/Event objects) — that's not a
        bug; a real folder-item array in any of the `raw_folder_*`/
        `raw_materials_*` keys is what actually matters here. The
        `raw_materials_*` candidates aren't in the public API docs, but the
        web UI's own materials tab (`.../course/{section_id}/materials` —
        "course" there is the web UI's name for what the API calls a
        section) suggests an undocumented sections-scoped resource by that
        name might exist.

        `query` narrows which section(s) to probe by a case-insensitive
        substring of the display name (e.g. "AP Physics") — every matching
        section is probed (a class split into a main + prep-lab section both
        match), since which one is "the" section isn't always obvious from
        outside Schoology. With no query, probes just the first academic
        section found, matching the original single-section behavior."""
        integration = await self._load_integration(user_id)
        client = await self._client(integration)

        async def _try(path: str) -> Any:
            try:
                return await client.get_raw(path)
            except Exception as e:  # noqa: BLE001
                return {"error": str(e)}

        try:
            uid = await client.current_user_id()
            sections = await client.get_sections(uid)
            academic = [
                s for s in sections
                if not course_mapping.is_excluded(s.display_name)
                and not course_mapping.is_club(s.display_name)
            ]
            if not academic:
                return {"sections_found": len(sections), "note": "No academic sections to probe."}

            if query:
                q = query.strip().lower()
                matches = [s for s in academic if q in s.display_name.lower()]
                if not matches:
                    return {
                        "note": f"No section matched {query!r}.",
                        "available_sections": [s.display_name for s in academic],
                    }
            else:
                matches = [academic[0]]

            probed = []
            for s in matches:
                course_realm_id = s.course_id or s.id
                probed.append({
                    "section": {"id": s.id, "name": s.display_name, "course_id": s.course_id},
                    "raw_assignments": await _try(f"/sections/{s.id}/assignments?with_attachments=1&limit=200"),
                    "raw_events": await _try(f"/sections/{s.id}/events?limit=200"),
                    "raw_course_detail": await _try(f"/courses/{course_realm_id}"),
                    "raw_folder_courses_realm": await _try(f"/courses/{course_realm_id}/folder/0"),
                    "raw_folder_sections_realm": await _try(f"/sections/{s.id}/folder/0"),
                    "raw_folder_sections_realm_no_id": await _try(f"/sections/{s.id}/folder"),
                    # Not in the public docs, but the web UI's own materials
                    # tab (https://<district>.schoology.com/course/{section_id}
                    # /materials — "course" here is the web UI's name for what
                    # the API calls a section) suggests an undocumented
                    # sections-scoped materials resource might exist.
                    "raw_materials_sections_realm": await _try(f"/sections/{s.id}/materials"),
                    "raw_materials_sections_realm_root": await _try(f"/sections/{s.id}/materials/0"),
                })
            return {"probed": probed}
        finally:
            await client.aclose()

    # ---- materials scraper (bypasses the blocked Courses-realm API) ----
    async def _scraper_client(self, user_id: str) -> SchoologyScraperClient:
        """Load this user's saved materials-scraper login (if configured) and
        return a logged-in client. Raises a clear error if materials-scraper
        credentials haven't been saved yet, or if the domain is missing, or
        if login itself fails — every case a caller needs to distinguish."""
        integration = await self._load_integration(user_id)
        creds = decrypt_json(integration["secret_ref"])
        if not creds.get("schoology_username") or not creds.get("schoology_password"):
            raise RuntimeError(
                "Materials access isn't connected yet — add your Schoology username "
                "and password under \"Materials access\" first."
            )
        config = integration.get("config") or {}
        domain = config.get("domain")
        if not domain:
            raise RuntimeError(
                "Materials access needs your Schoology web address (e.g. "
                "https://yourdistrict.schoology.com) — add it in the Schoology connect form."
            )
        client = SchoologyScraperClient(domain, creds["schoology_username"], creds["schoology_password"])
        try:
            await client.login()
        except SchoologyScraperAuthError:
            await client.aclose()
            raise
        return client

    async def verify_materials_login(self, user_id: str) -> dict[str, Any]:
        """Confirm the saved materials-scraper login actually works — used
        right after saving it, so a typo'd password surfaces immediately
        instead of silently failing on the next scheduled sync."""
        client = await self._scraper_client(user_id)
        await client.aclose()
        return {"status": "success"}

    async def _probe_sections(
        self, user_id: str, query: str | None,
    ) -> tuple[list[dict[str, str]], str | None, dict[str, Any] | None]:
        """Sections to run a materials debug probe against, for
        `debug_scrape_materials`/`debug_walk_materials`. Prefers the API
        (which also yields the account's own user id, needed for the
        app.schoology.com preview-URL candidate) but falls back to whatever
        courses a prior sync already linked (`metadata.schoology_section_id`)
        when there's no API key on file, or the API itself rejects it — so
        these debug tools stay usable on a login-only account instead of
        hard-failing on the very API call `sync()` no longer even attempts
        for materials. Returns `(sections, uid, early_result)` — when
        there's nothing to probe or the query matched nothing, `early_result`
        is set and the other two should be ignored."""
        integration = await self._load_integration(user_id)

        # Fast path: when the request is for (or defaults to) one of the
        # confirmed-real courses, course_mapping.KNOWN_SECTIONS already has
        # everything needed — id, name, and the exact materials_url — with
        # no API call, DB lookup, or login-session discovery required to
        # find it. Skipping straight to it also matters operationally: each
        # of the discovery fallbacks below (`_scraper_client` calls
        # `client.login()`, and `list_courses()` additionally logs into
        # app.schoology.com) is a *separate* login POST to Schoology, so the
        # slow path can rack up 3-4 logins in a single debug call. Enough
        # repeated automated logins in a short window is exactly the kind of
        # pattern that trips a site's own bot/abuse detection, which then
        # serves a challenge page instead of the normal login form — that
        # looks identical to "this district enforces SSO", even though
        # nothing about the account or district actually changed.
        known_matches = [
            k for k in course_mapping.KNOWN_SECTIONS
            if not query or query.strip().lower() in k["name"].lower()
        ]
        if known_matches:
            sections = [
                {"id": k["id"], "name": k["name"], "materials_url": k.get("materials_url")}
                for k in known_matches
            ]
            uid = next((k["student_uid"] for k in known_matches if k.get("student_uid")), None)
            return sections, uid, None

        uid: str | None = None
        sections: list[dict[str, str]] = []
        if self._has_api_key(integration):
            try:
                api_client = await self._client(integration)
                try:
                    uid = await api_client.current_user_id()
                    raw_sections = await api_client.get_sections(uid)
                finally:
                    await api_client.aclose()
                sections = [
                    {"id": s.id, "name": s.display_name}
                    for s in raw_sections
                    if not course_mapping.is_excluded(s.display_name)
                    and not course_mapping.is_club(s.display_name)
                ]
            except Exception:  # noqa: BLE001
                sections = []  # fall through to the linked-courses fallback below

        if not sections:
            rows = await supabase.select(
                "courses", columns="name,metadata", filters={"user_id": eq(user_id)},
            ) or []
            sections = [
                {"id": (r.get("metadata") or {}).get("schoology_section_id"), "name": r["name"]}
                for r in rows if (r.get("metadata") or {}).get("schoology_section_id")
            ]
            # A parent account's student uid was persisted on the course
            # during discovery — read it back so the walk hits the parent-view
            # preview URL, not the (empty) plain /materials page. Without this,
            # an already-linked parent course fell back to /materials and came
            # back with nothing.
            if not uid:
                uid = next(
                    (
                        (r.get("metadata") or {}).get("schoology_student_uid")
                        for r in rows
                        if (r.get("metadata") or {}).get("schoology_student_uid")
                    ),
                    None,
                )
            # Still no uid (e.g. courses linked before parent support existed,
            # so the uid was never stored) — discover it straight from the
            # login session so the probe can still reach the parent pages.
            if not uid:
                try:
                    scraper = await self._scraper_client(user_id)
                    try:
                        discovered = await scraper.list_courses()
                    finally:
                        await scraper.aclose()
                    uid = next(
                        (c.get("student_uid") for c in discovered if c.get("student_uid")), None
                    )
                except Exception:  # noqa: BLE001
                    pass

        if not sections:
            # Login-only account with nothing linked yet: discover the
            # enrolled courses straight from the web session so the debug
            # tools (and sync) work without ever needing an API key.
            try:
                scraper = await self._scraper_client(user_id)
                try:
                    discovered = await scraper.list_courses()
                finally:
                    await scraper.aclose()
            except Exception as e:  # noqa: BLE001
                return [], None, {"note": f"Could not discover courses from the Schoology login: {e}"}
            # A parent account's course links carry the student's user id —
            # capture it so the materials walk can reach the parent-view
            # pages (a parent's real materials live only there).
            uid = next((c.get("student_uid") for c in discovered if c.get("student_uid")), None)
            sections = [
                {"id": c["id"], "name": c["name"]}
                for c in discovered
                if not course_mapping.is_excluded(c["name"])
                and not course_mapping.is_club(c["name"])
            ]

        # Merge in the confirmed-real course links (course_mapping.
        # KNOWN_SECTIONS) no matter how `sections` above was populated — the
        # API listing, the DB's already-linked courses, and a fresh
        # login-session discovery have each, at different times, come back
        # incomplete or empty for this account even though every one of
        # these courses is reachable directly by URL (the reported "still
        # not pulling any links or the correct information"). A section
        # already found above is left as-is; a known one missing from it is
        # added, and a missing `uid` is backfilled — so the debug tools can
        # always reach these courses' real materials pages regardless of
        # whether discovery itself is working. Deliberately scoped to the
        # debug probe only, not `_discover_and_link_sections` (the automated
        # sync) — this table is a diagnostic aid for confirming discovery
        # against confirmed-real links, not a substitute for fixing
        # discovery itself for every account's real sync.
        sections, uid = course_mapping.merge_known_sections(sections, uid)
        if not sections:
            return [], None, {
                "note": "No Schoology courses found for this login. If you use a parent "
                        "account, its courses may live on app.schoology.com — otherwise "
                        "check the account is enrolled in courses.",
            }

        if query:
            q = query.strip().lower()
            matches = [s for s in sections if q in s["name"].lower()]
            if not matches:
                return [], None, {
                    "note": f"No section matched {query!r}.",
                    "available_sections": [s["name"] for s in sections],
                }
            sections = matches
        else:
            # No query: default to every confirmed-real course
            # (course_mapping.KNOWN_SECTIONS) rather than an arbitrary single
            # "first" section — with several of these ids already present
            # (correctly or not) from the API/DB/discovery above, "the first
            # one found" was never guaranteed to be one of the 9 confirmed
            # courses, so the debug screen's default view could show an
            # unrelated (or stale) course instead of the ones actually being
            # verified. Falls back to the old single-first-section behavior
            # only when there are no known sections to default to.
            known_ids = [k["id"] for k in course_mapping.KNOWN_SECTIONS]
            by_id = {s["id"]: s for s in sections}
            defaulted = [by_id[kid] for kid in known_ids if kid in by_id]
            sections = defaulted or [sections[0]]

        return sections, uid, None

    async def debug_scrape_materials(self, user_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetch one or more sections' materials page after logging in as the
        student, verbatim — mirrors `debug_fetch`'s query-matching, but for
        the scraper path. Used to confirm the real authenticated page shape
        (title/links/HTML snippet) before writing a parser against it, rather
        than guessing — the API side of this integration burned several
        rounds guessing at endpoints instead of verifying, so this one is
        built diagnostic-first."""
        sections, uid, early = await self._probe_sections(user_id, query)
        if early is not None:
            return early

        scraper = await self._scraper_client(user_id)
        try:
            probed = []
            for s in sections:
                probed.append({
                    "section": {"id": s["id"], "name": s["name"]},
                    "materials_page": await scraper.debug_materials_page(s["id"], student_uid=uid),
                })
            return {"probed": probed}
        finally:
            await scraper.aclose()

    async def debug_walk_materials(self, user_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetch one or more sections' materials pages via the scraper login,
        walk every folder, and report the classified result — real folders
        recursed into, real items returned, Schoology's page chrome (nav, app
        launchers, type filters, admin/export links) filtered out. Lets
        `schoology_scraper.parse_materials_page`'s classification be
        confirmed against a real account before `sync()` is trusted to rely
        on it (see `_sync_scraped_materials`)."""
        sections, uid, early = await self._probe_sections(user_id, query)
        if early is not None:
            return early

        scraper = await self._scraper_client(user_id)
        try:
            probed = []
            for s in sections:
                trace: list[dict[str, Any]] = []
                materials_url = s.get("materials_url")
                error: str | None = None
                try:
                    if materials_url:
                        # A confirmed-real course (course_mapping.
                        # KNOWN_SECTIONS) — walk exactly that URL and
                        # nothing else, instead of guessing across every
                        # candidate shape.
                        items = await scraper.walk_known_url(materials_url, trace=trace)
                    else:
                        items = await scraper.walk_materials(s["id"], student_uid=uid, trace=trace)
                except Exception as e:  # noqa: BLE001
                    # One course's walk failing (a network blip, an
                    # unexpected page shape, …) must not blank out every
                    # other course's real results — report the error for
                    # just this one and keep going, the same resilience the
                    # per-course sync loop already has.
                    items = []
                    error = str(e)
                item_dicts = []
                for i in items:
                    item_dict: dict[str, Any] = {
                        "name": i.name, "type": i.material_type or None,
                        "folder": i.folder_path or None, "href": i.href,
                    }
                    # Actually attempt the download for anything the real
                    # sync would (see _ingest_scraped_material's
                    # _SCRAPED_FILE_TYPES gate) — using the exact same
                    # scraper.download_file call the real sync uses, so the
                    # debug screen gives a definitive yes/no on whether a
                    # document was really downloaded instead of just
                    # listing the item and leaving that as a guess.
                    if (i.material_type or "").lower() in self._SCRAPED_FILE_TYPES:
                        try:
                            downloaded = await scraper.download_file(i.href)
                        except Exception as e:  # noqa: BLE001
                            downloaded = None
                            item_dict["download_error"] = str(e)
                        if downloaded:
                            content, content_type = downloaded
                            item_dict["downloaded"] = True
                            item_dict["download_content_type"] = content_type
                            item_dict["download_size_bytes"] = len(content)
                        else:
                            item_dict["downloaded"] = False
                    item_dicts.append(item_dict)
                probed.append({
                    "section": {"id": s["id"], "name": s["name"]},
                    # The exact URL walked, when it's a confirmed course
                    # (course_mapping.KNOWN_SECTIONS) — so it's visible on
                    # screen that this is really hitting the given link,
                    # not a guess, whether or not it found any items.
                    "materials_url": materials_url,
                    "items": item_dicts,
                    # Every page the walk actually visited (url, status, how
                    # many links it saw / parsed, whether it hit a login
                    # wall) — so an empty `items` list is diagnosable instead
                    # of just blank.
                    "walk_trace": trace,
                    **({"error": error} if error else {}),
                })
            return {"probed": probed}
        finally:
            await scraper.aclose()

    # ---- course reconciliation ----
    async def _resolve_club(self, user_id: str, section: SchoologySection) -> str:
        """Clubs (DECA, etc.) get their own table — never mixed into GPA/
        course data. See `course_mapping.is_club`."""
        return await self.upsert_club(user_id, section.id, {
            "name": section.display_name,
            "meeting_info": section.location or None,
            "metadata": {
                "schoology_section_id": section.id,
                "meeting_days": section.meeting_days,
            },
        })

    async def _resolve_grouped_course(
        self, user_id: str, section: SchoologySection,
        group: course_mapping.CourseGroup, member: course_mapping.GroupMember,
    ) -> str:
        """Resolve/create the merged course row for a section matched by
        `course_mapping.match_group` (e.g. an "HN Ext Lab" + "AP" pair that
        should display as one course with linked semester rows).

        Also self-heals accounts synced before this grouping existed: if this
        exact section was already imported as its own standalone course, that
        row is renamed/relabeled into the group in place (via the external_id
        match below) rather than left as a stale duplicate — no manual
        course cleanup required after a re-sync."""
        existing = await supabase.select(
            "courses", columns="id,metadata",
            filters={"user_id": eq(user_id), "external_id": eq(section.id),
                     "external_source": eq(self.name)}, limit=1,
        )
        group_rows = await supabase.select(
            "courses", columns="id,semester,linked_course_id,metadata",
            filters={"user_id": eq(user_id), "metadata->>course_group": eq(group.key)},
        ) or []

        patch: dict[str, Any] = {
            "name": group.canonical_name,
            "code": section.course_code or None,
            "room": section.location or None,
            "semester": member.semester,
            "course_level": member.course_level,
            "has_hn_prep_lab": member.has_hn_prep_lab,
            "has_ap_prep_lab": member.has_ap_prep_lab,
            "external_id": section.id,
            "external_source": self.name,
        }
        meta_extra = {
            "course_group": group.key,
            "schoology_section_id": section.id,
            "meeting_days": section.meeting_days,
            "start_time": section.start_time,
            "end_time": section.end_time,
        }

        if existing:
            row_id = existing[0]["id"]
            meta = {**(existing[0].get("metadata") or {}), **meta_extra}
            await supabase.update("courses", {**patch, "metadata": meta}, filters={"id": eq(row_id)})
            return row_id

        # Reuse the group's row for this semester if one already exists
        # (e.g. re-syncing the same section under a slightly different id).
        same_semester = next((r for r in group_rows if r.get("semester") == member.semester), None)
        if same_semester:
            meta = {**(same_semester.get("metadata") or {}), **meta_extra}
            await supabase.update(
                "courses", {**patch, "metadata": meta}, filters={"id": eq(same_semester["id"])}
            )
            return same_semester["id"]

        # First row for the group becomes the root; later semesters link to it.
        root = next((r for r in group_rows if not r.get("linked_course_id")), None)
        patch["metadata"] = meta_extra
        if root:
            patch["linked_course_id"] = root["id"]
        created = await supabase.insert("courses", {**patch, "user_id": user_id})
        return created[0]["id"]

    async def _refresh_active_status(self, course_id: str, active: bool) -> None:
        """Best-effort: keep `is_active` current (the signal that moves a
        class between "current" and "completed" in the UI) without ever
        blocking the rest of the sync if this particular write fails — e.g. a
        migration not yet applied to this project's database, or any other
        transient error. Assignments/materials/grades are the important part
        of a sync; a cosmetic status flag must never be able to take the
        whole course down with it."""
        try:
            await supabase.update("courses", {"is_active": active}, filters={"id": eq(course_id)})
        except Exception:  # noqa: BLE001
            pass

    async def _resolve_course(
        self, user_id: str, section: SchoologySection, present_group_semesters: dict[str, set[str]],
    ) -> str:
        """Return the course_id this Schoology section maps to (see
        `_resolve_course_id`), then best-effort refresh its active status."""
        course_id = await self._resolve_course_id(user_id, section, present_group_semesters)
        await self._refresh_active_status(course_id, section.active)
        return course_id

    async def _resolve_course_id(
        self, user_id: str, section: SchoologySection, present_group_semesters: dict[str, set[str]],
    ) -> str:
        """Reuse an existing PowerSchool/manual/prior-Schoology course when one
        matches so the systems share a single course row. Only creates a new
        course when nothing matches."""
        group_match = course_mapping.match_group(section.display_name)
        if group_match:
            group, member = group_match
            # Only actually split into the group when there's real evidence
            # this class is split this way: either the section itself is the
            # distinctively-named HN prep-lab half (a name like "Physics 1 H
            # Ext Lab" doesn't happen to a plain, already-existing course), or
            # both halves showed up in this same sync. Otherwise a plain,
            # already-reconciled course (e.g. a stand-alone "AP Biology" with
            # no lab counterpart) would get needlessly split — fall through
            # to ordinary name-based reconciliation instead.
            if member.has_hn_prep_lab or len(present_group_semesters.get(group.key, set())) >= 2:
                return await self._resolve_grouped_course(user_id, section, group, member)

        # 1) A course this provider already created/linked for this section.
        existing = await supabase.select(
            "courses", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(section.id),
                     "external_source": eq(self.name)}, limit=1,
        )
        if existing:
            return existing[0]["id"]
        linked = await supabase.select(
            "courses", columns="id",
            filters={"user_id": eq(user_id),
                     "metadata->>schoology_section_id": eq(section.id)}, limit=1,
        )
        if linked:
            return linked[0]["id"]

        # 2) An existing course (any source) whose name matches — link, don't dupe.
        all_courses = await supabase.select(
            "courses", columns="id,name,code,metadata", filters={"user_id": eq(user_id)},
        )
        for c in all_courses or []:
            if _names_match(section.display_name, c.get("name") or "") or (
                section.course_code and section.course_code == (c.get("code") or "")
            ):
                meta = {**(c.get("metadata") or {}), "schoology_section_id": section.id,
                        "schoology_web_url": section.raw.get("profile_url")}
                patch: dict[str, Any] = {"metadata": meta}
                # Fill in scheduling details PowerSchool doesn't provide, if empty.
                if section.location and not (c.get("metadata") or {}).get("room"):
                    patch.setdefault("room", section.location)
                await supabase.update("courses", patch, filters={"id": eq(c["id"])})
                return c["id"]

        # 3) No match — create a Schoology-owned course. course_level is only
        # a *default inferred from the name* (e.g. "AP English Lang" -> ap),
        # applied on creation only so it never overrides a level the user
        # sets afterward.
        return await self.upsert_course(user_id, section.id, {
            "name": section.display_name,
            "code": section.course_code or None,
            "room": section.location or None,
            "metadata": {
                "schoology_section_id": section.id,
                "meeting_days": section.meeting_days,
                "start_time": section.start_time,
                "end_time": section.end_time,
            },
        }, create_only={"course_level": course_mapping.infer_course_level(section.display_name)})

    # ---- week-at-a-glance ----
    async def _upsert_calendar_event(
        self, user_id: str, external_id: str, fields: dict[str, Any]
    ) -> None:
        existing = await supabase.select(
            "calendar_events", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(external_id)}, limit=1,
        )
        payload = {**fields, "user_id": user_id, "external_id": external_id}
        if existing:
            await supabase.update("calendar_events", payload, filters={"id": eq(existing[0]["id"])})
        else:
            await supabase.insert("calendar_events", payload)

    @staticmethod
    def _in_week(iso: str | None, monday: date, sunday: date) -> bool:
        if not iso:
            return False
        try:
            d = datetime.fromisoformat(iso).date()
        except ValueError:
            return False
        return monday <= d <= sunday

    # ---- material ingestion ----
    async def _document_exists(self, user_id: str, external_id: str) -> bool:
        rows = await supabase.select(
            "documents", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(external_id),
                     "external_source": eq(self.name)}, limit=1,
        )
        return bool(rows)

    async def _ingest_file(
        self, *, user_id: str, course_id: str, external_id: str, title: str,
        content: bytes, filename: str, content_type: str, doc_type: str = "other",
        extra_meta: dict[str, Any] | None = None,
    ) -> bool:
        """Store + text-extract + embed a binary file (pdf/pptx/doc/…). Returns
        True if newly ingested, False if it already existed (idempotent).

        Uploads the original bytes to R2 the same way a direct upload does
        (`routers/documents.py`'s `_store_and_ingest`) so the file has a
        `storage_path` and is actually viewable/downloadable from the app,
        not just searchable as extracted text — best-effort, same as there:
        a storage failure still lets the document be recorded and indexed."""
        if await self._document_exists(user_id, external_id):
            return False
        doc_id = str(uuid.uuid4())
        storage_path = f"{user_id}/{doc_id}/{safe_object_name(filename)}"
        try:
            await r2.upload(storage_path, content, content_type or "application/octet-stream")
        except Exception:  # noqa: BLE001
            storage_path = None
        text = ""
        try:
            # extract_text is a synchronous, CPU-bound call (PyMuPDF layout
            # reconstruction for PDFs can take real wall-clock time on a big
            # or complex file) — run it off the event loop thread so it can't
            # block other awaits, including the asyncio.wait_for() timeout
            # that wraps the whole sync() call in app.integrations.run_sync:
            # a blocking call has no await point for that timeout to cancel
            # at, so it would otherwise defeat the timeout entirely.
            text = await asyncio.to_thread(ingestion.extract_text, content, content_type, filename)
        except Exception:  # noqa: BLE001
            text = ""
        await supabase.insert("documents", {
            "id": doc_id, "user_id": user_id, "course_id": course_id,
            "title": title or filename or "Untitled", "doc_type": doc_type,
            "mime_type": content_type, "size_bytes": len(content),
            "storage_path": storage_path,
            "external_id": external_id, "external_source": self.name,
            "metadata": extra_meta or {},
        })
        try:
            await ingestion.ingest_document(doc_id, user_id, text)
        except Exception as e:  # noqa: BLE001
            await supabase.update(
                "documents", {"ingested": False, "ingest_error": str(e)[:400]},
                filters={"id": eq(doc_id)},
            )
        # Same enrichment (summary/keywords/doc_type + concept links) a direct
        # upload gets via `_store_and_ingest` — best-effort, and never renames
        # the title: Schoology's own material name is already the right title,
        # unlike a direct upload's filename-derived placeholder.
        if settings.has_llm and text.strip():
            try:
                await Archivist().enrich(user_id, doc_id, text)
            except Exception:  # noqa: BLE001
                pass
        return True

    async def _ingest_text(
        self, *, user_id: str, course_id: str, external_id: str, title: str,
        text: str, doc_type: str = "other", extra_meta: dict[str, Any] | None = None,
    ) -> bool:
        if await self._document_exists(user_id, external_id):
            return False
        doc_id = str(uuid.uuid4())
        await supabase.insert("documents", {
            "id": doc_id, "user_id": user_id, "course_id": course_id,
            "title": title or "Untitled", "doc_type": doc_type,
            "size_bytes": len(text or ""),
            "external_id": external_id, "external_source": self.name,
            "metadata": extra_meta or {},
        })
        try:
            await ingestion.ingest_document(doc_id, user_id, text or title)
        except Exception:  # noqa: BLE001
            pass
        return True

    async def _ingest_attachments(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        owner_external_id: str, attachments: dict[str, Any],
        google_token: str | None, report: dict[str, Any],
    ) -> None:
        """Ingest every file + link attached to an assignment/material/update."""
        for f in files_of(attachments):
            fid = str(f.get("id") or "")
            download_path = f.get("download_path")
            if not download_path:
                continue
            ext_id = f"{owner_external_id}:file:{fid}"
            try:
                content = await client.download_file(download_path)
                filename = f.get("filename") or f.get("title") or f"file-{fid}"
                if await self._ingest_file(
                    user_id=user_id, course_id=course_id, external_id=ext_id,
                    title=f.get("title") or filename, content=content, filename=filename,
                    content_type="application/octet-stream", doc_type="other",
                ):
                    report["documents"] += 1
            except Exception as e:  # noqa: BLE001
                report["errors"].append(f"file {fid}: {e}")

        for l in links_of(attachments):
            lid = str(l.get("id") or "")
            url = l.get("url") or ""
            if not url:
                continue
            ext_id = f"{owner_external_id}:link:{lid}"
            await self._ingest_link(
                client=client, user_id=user_id, course_id=course_id, external_id=ext_id,
                title=l.get("title") or url, url=url, google_token=google_token, report=report,
            )

    async def _ingest_link(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        external_id: str, title: str, url: str, google_token: str | None,
        report: dict[str, Any],
    ) -> None:
        """A linked resource. Google Docs/Slides/Sheets are fully downloaded &
        ingested when a Google token is available; otherwise (and for other
        links) the link itself is recorded as searchable knowledge and flagged."""
        if is_google_url(url):
            ref = parse_google_url(url)
            if ref and google_token:
                try:
                    content, filename, content_type = await download_google_file(
                        ref, google_token, name=title
                    )
                    if await self._ingest_file(
                        user_id=user_id, course_id=course_id, external_id=external_id,
                        title=title, content=content, filename=filename,
                        content_type=content_type, doc_type="other",
                        extra_meta={"source_url": url, "google_file_id": ref.file_id},
                    ):
                        report["documents"] += 1
                    return
                except Exception as e:  # noqa: BLE001
                    report["errors"].append(f"google {ref.file_id}: {e}")
                    # fall through to storing the link
            # No token (or download failed): record the link, flag for auth.
            if await self._ingest_text(
                user_id=user_id, course_id=course_id, external_id=external_id,
                title=title, text=f"{title}\n{url}", doc_type="other",
                extra_meta={"source_url": url, "needs_google_auth": True},
            ):
                report["links"] += 1
            return
        # Non-Google link: store the reference as knowledge.
        if await self._ingest_text(
            user_id=user_id, course_id=course_id, external_id=external_id,
            title=title, text=f"{title}\n{url}", doc_type="other",
            extra_meta={"source_url": url},
        ):
            report["links"] += 1

    # ---- main sync ----
    async def sync(self, user_id: str) -> dict[str, Any]:
        integration = await self._load_integration(user_id)
        report: dict[str, Any] = {
            "courses": 0, "clubs": 0, "excluded": 0, "assignments": 0, "events": 0,
            "documents": 0, "links": 0, "announcements": 0, "errors": [],
        }

        # The login (username/password) is the only required credential now;
        # the API key is an optional extra (see `SchoologyConnectRequest`).
        # Without it there's no way yet to discover courses or read
        # assignments/events — no scraper exists for those, only for
        # materials (`schoology_scraper.py`) — so this refreshes materials
        # for whatever courses a *previous* API-connected sync already
        # linked, instead of syncing nothing at all.
        if not self._has_api_key(integration):
            await self._sync_materials_only(user_id, integration, report)
            return report

        config = integration.get("config") or {}
        google_token = config.get("google_access_token")  # optional, for Drive downloads
        client = await self._client(integration)
        # Materials still only ever come from the login-scraper session (see
        # module docstring), but logged in *once* here and reused for every
        # course below — logging in separately per course (the previous
        # behavior) made a many-course account's sync time scale with course
        # count just from repeated logins, which is what pushed some syncs
        # past the platform's request timeout. A login failure here doesn't
        # abort the sync: assignments/events (API-backed) still run per
        # section, just without materials for this run.
        scraper: SchoologyScraperClient | None = None
        try:
            scraper = await self._scraper_client(user_id)
        except SchoologyScraperAuthError as e:
            report["errors"].append(f"Schoology materials (scrape login): {e}")
        except RuntimeError as e:
            if "isn't connected yet" not in str(e):
                report["errors"].append(f"Schoology materials (scrape login): {e}")
        try:
            uid = await client.current_user_id()
            sections = await client.get_sections(uid)
            monday, sunday = week_bounds()

            present_group_semesters: dict[str, set[str]] = {}
            for s in sections:
                gm = course_mapping.match_group(s.display_name)
                if gm:
                    g, m = gm
                    present_group_semesters.setdefault(g.key, set()).add(m.semester)

            # Resolving each section to a course_id is a single cheap DB
            # upsert, done up front and in order; the actual slow part —
            # assignments/events/materials — is collected into a task list
            # and run concurrently below instead of one course at a time.
            section_tasks: list[Any] = []
            for section in sections:
                # Non-academic blocks (lunch, advisory) — never imported, and
                # any stale row from before this filter existed is removed.
                if course_mapping.is_excluded(section.display_name):
                    try:
                        await supabase.delete(
                            "courses",
                            filters={"user_id": eq(user_id), "external_id": eq(section.id),
                                     "external_source": eq(self.name)},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    report["excluded"] += 1
                    continue

                # Clubs/activities — tracked separately, never as a course.
                if course_mapping.is_club(section.display_name):
                    try:
                        await self._resolve_club(user_id, section)
                        report["clubs"] += 1
                    except Exception as e:  # noqa: BLE001
                        report["errors"].append(f"{section.display_name} (club): {e}")
                    continue

                try:
                    course_id = await self._resolve_course(user_id, section, present_group_semesters)
                    report["courses"] += 1
                except Exception as e:  # noqa: BLE001
                    report["errors"].append(f"{section.display_name}: {e}")
                    continue

                section_tasks.append(self._sync_section(
                    client=client, user_id=user_id, course_id=course_id, section=section,
                    monday=monday, sunday=sunday, google_token=google_token,
                    student_uid=uid, report=report, scraper=scraper,
                ))

            if section_tasks:
                await _gather_bounded(section_tasks, limit=_SECTION_SYNC_CONCURRENCY)

            return report
        finally:
            await client.aclose()
            if scraper is not None:
                await scraper.aclose()

    async def _sync_materials_only(self, user_id: str, integration: dict[str, Any], report: dict[str, Any]) -> None:
        """No API key on file — refresh materials via the login-scraper
        session. Uses whatever courses Atlas already linked a Schoology
        section id for (`metadata.schoology_section_id`) and, when nothing is
        linked, discovers the enrolled courses straight from the login
        session (`_discover_and_link_sections`) so a fully login-only account
        works without ever needing the API. For a parent account the walk
        also needs the student's user id (to reach the parent-view materials
        pages) — that's carried on each course's `metadata.schoology_student_uid`,
        captured during discovery. Logs in once for the whole run — that one
        session is shared by discovery and every course's materials walk
        below, rather than re-logging-in per course."""
        config = integration.get("config") or {}
        google_token = config.get("google_access_token")
        try:
            scraper = await self._scraper_client(user_id)
        except SchoologyScraperAuthError as e:
            report["errors"].append(f"Schoology materials (scrape login): {e}")
            return
        except RuntimeError as e:
            report["errors"].append(str(e))
            return

        try:
            # Discover from the login session every sync — it's the source of
            # truth for section ids, names, and (for a parent account) the
            # student uid, and it reconciles/links each course while backfilling
            # metadata.schoology_student_uid. Running it unconditionally is what
            # gives an already-linked parent course (linked before parent support
            # existed, so with no student uid stored) its uid — without which the
            # walk falls back to the empty plain /materials page.
            try:
                linked = await self._discover_and_link_sections(user_id, report, scraper)
            except SchoologyScraperAuthError as e:
                report["errors"].append(f"Schoology materials (scrape login): {e}")
                linked = []
            except RuntimeError as e:
                report["errors"].append(str(e))
                linked = []

            if not linked:
                # Discovery found nothing — fall back to whatever a prior
                # sync already linked so those still refresh.
                rows = await supabase.select(
                    "courses", columns="id,name,metadata", filters={"user_id": eq(user_id)},
                ) or []
                linked = [
                    (r["id"], r["name"], (r.get("metadata") or {}).get("schoology_section_id"),
                     (r.get("metadata") or {}).get("schoology_student_uid"))
                    for r in rows
                ]
                linked = [(cid, name, sid, uid) for cid, name, sid, uid in linked if sid]
            if not linked:
                report["errors"].append(
                    "No Schoology courses found for this login. If this is a parent account, "
                    "its courses may live on app.schoology.com; otherwise confirm the account "
                    "is enrolled in courses."
                )
                return
            async def _sync_one(course_id: str, name: str, section_id: str, student_uid: str | None) -> None:
                section = SchoologySection(
                    id=section_id, course_id=section_id, course_title=name, section_title="",
                    course_code="", section_code="", grading_periods=[], meeting_days=[],
                    start_time="", end_time="", location="", active=True,
                )
                await self._sync_scraped_materials(
                    user_id=user_id, course_id=course_id, section=section,
                    report=report, scraper=scraper, google_token=google_token,
                    student_uid=student_uid,
                )
                report["courses"] += 1

            # See `sync()`'s section_tasks comment — same reasoning, walking
            # each course's materials concurrently instead of one at a time.
            await _gather_bounded(
                [_sync_one(*item) for item in linked], limit=_SECTION_SYNC_CONCURRENCY,
            )
        finally:
            await scraper.aclose()

    async def _discover_and_link_sections(
        self, user_id: str, report: dict[str, Any], scraper: SchoologyScraperClient,
    ) -> list[tuple[str, str, str, str | None]]:
        """Discover enrolled courses from the login session (no API key) and
        reconcile each into an Atlas course row — reusing the same
        name-matching/creation path (`_resolve_course`) the API sync uses, so
        a Schoology section still links to an existing PowerSchool/manual
        course instead of duplicating it, and `metadata.schoology_section_id`
        gets set for next time. A parent account's course links also carry the
        student's user id; it's persisted as `metadata.schoology_student_uid`
        and returned so the materials walk can reach the parent-view pages.
        Returns `(course_id, name, section_id, student_uid)` for every
        academic course found; clubs go to their own table, excluded blocks
        are skipped — matching `sync()`'s behavior. `scraper` is the caller's
        already-logged-in session (shared with the materials walk that
        follows) — this method does not close it."""
        discovered = await scraper.list_courses()

        # Merge in the confirmed-real course links (course_mapping.
        # KNOWN_SECTIONS) so these courses get linked and synced every time,
        # even on a run where the login-session course-list crawl comes back
        # incomplete or empty for this account. A course discovery already
        # found is left as-is; `_sync_scraped_materials` separately looks up
        # `materials_url_for` by id to walk the exact confirmed link instead
        # of guessing, for both these and any course discovery adds later.
        discovered, _ = course_mapping.merge_known_sections(discovered)

        linked: list[tuple[str, str, str, str | None]] = []
        for c in discovered:
            name, sid = c["name"], c["id"]
            student_uid = c.get("student_uid")
            if course_mapping.is_excluded(name):
                report["excluded"] += 1
                continue
            section = SchoologySection(
                id=sid, course_id=sid, course_title=name, section_title="",
                course_code="", section_code="", grading_periods=[], meeting_days=[],
                start_time="", end_time="", location="", active=True,
            )
            if course_mapping.is_club(name):
                try:
                    await self._resolve_club(user_id, section)
                    report["clubs"] += 1
                except Exception as e:  # noqa: BLE001
                    report["errors"].append(f"{name} (club): {e}")
                continue
            try:
                course_id = await self._resolve_course(user_id, section, {})
                if student_uid:
                    # Persist the student uid so later materials-only syncs
                    # (which re-read from the DB, not discovery) keep reaching
                    # the parent-view pages.
                    await self._merge_course_metadata(
                        course_id, {"schoology_student_uid": student_uid}
                    )
            except Exception as e:  # noqa: BLE001
                report["errors"].append(f"{name}: {e}")
                continue
            linked.append((course_id, name, sid, student_uid))
        return linked

    async def _merge_course_metadata(self, course_id: str, extra: dict[str, Any]) -> None:
        """Best-effort shallow-merge into a course's metadata JSON, preserving
        whatever's already there. Never lets a metadata write break a sync."""
        try:
            rows = await supabase.select(
                "courses", columns="metadata", filters={"id": eq(course_id)}, limit=1,
            )
            meta = {**((rows[0].get("metadata") if rows else None) or {}), **extra}
            await supabase.update("courses", {"metadata": meta}, filters={"id": eq(course_id)})
        except Exception:  # noqa: BLE001
            pass

    async def _sync_section(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        section: SchoologySection, monday: date, sunday: date,
        google_token: str | None, scraper: SchoologyScraperClient | None,
        student_uid: str | None = None, report: dict[str, Any],
    ) -> None:
        sid = section.id

        # 1) Assignments — imported as work items (NO grades) + week-at-a-glance
        #    due dates + their attachments.
        try:
            assignments = await client.get_assignments(sid)
        except Exception as e:  # noqa: BLE001
            assignments = []
            report["errors"].append(f"{section.display_name} assignments: {e}")
        for a in assignments:
            await self._import_assignment(
                client=client, user_id=user_id, course_id=course_id, section=section,
                a=a, monday=monday, sunday=sunday, google_token=google_token, report=report,
            )

        # 2) Events — the section calendar (week-at-a-glance: what's happening
        #    each day this week). Assignment-linked events dedupe against the
        #    assignment's own due-date event.
        try:
            events = await client.get_events(sid)
        except Exception as e:  # noqa: BLE001
            events = []
            report["errors"].append(f"{section.display_name} events: {e}")
        for ev in events:
            if ev.assignment_id:
                continue  # already represented by the assignment's due event
            if not self._in_week(ev.start, monday, sunday):
                continue
            kind = "exam" if re.search(r"\b(exam|test|quiz)\b", ev.title, re.I) else "event"
            await self._upsert_calendar_event(user_id, f"schoology:event:{ev.id}", {
                "course_id": course_id, "title": ev.title,
                "description": ev.description or None,
                "starts_at": ev.start, "ends_at": ev.end, "all_day": ev.all_day,
                "kind": kind, "metadata": {"web_url": ev.web_url},
            })
            report["events"] += 1

        # 3) Every folder, recursively — files/slideshows/links/pages become
        #    searchable course knowledge. Always via the login-scraper
        #    session, never the API: the API's Courses-realm folder endpoint
        #    is commonly blocked outright for a student's personal key (a
        #    rejected key there produces a noisy per-course auth error, not
        #    just a quiet empty list), and the login is unconditionally on
        #    file now (see SchoologyConnectRequest) — there's no case left
        #    where the API is the only way to reach materials.
        #    `scraper` is None when the materials-login session couldn't be
        #    established for this run (see `sync()`) — skip materials for
        #    every section rather than trying (and failing) to log in again
        #    per course.
        if scraper is not None:
            await self._sync_scraped_materials(
                user_id=user_id, course_id=course_id, section=section, report=report,
                scraper=scraper, google_token=google_token, student_uid=student_uid,
            )

    async def _import_assignment(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        section: SchoologySection, a: SchoologyAssignment, monday: date, sunday: date,
        google_token: str | None, report: dict[str, Any],
    ) -> None:
        external_id = f"{section.id}:{a.id}"
        try:
            assignment_id = await self.upsert_assignment(user_id, external_id, {
                "course_id": course_id,
                "title": a.title,
                "description": a.description or None,
                "category": _map_category(f"{a.title} {a.assignment_type}"),
                "due_date": a.due,
                "points_possible": a.max_points,
                # Grading is PowerSchool-only — never write a grade/status here.
                "status": "not_started",
                "metadata": {"web_url": a.web_url, "schoology_assignment_id": a.id},
            })
            report["assignments"] += 1
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{section.display_name} · {a.title}: {e}")
            return

        # Week-at-a-glance: a due date this week becomes a calendar item.
        if self._in_week(a.due, monday, sunday):
            await self._upsert_calendar_event(user_id, f"schoology:due:{a.id}", {
                "course_id": course_id, "assignment_id": assignment_id,
                "title": f"Due: {a.title}", "starts_at": a.due, "all_day": False,
                "kind": "due", "metadata": {"web_url": a.web_url},
            })
            report["events"] += 1

        # Attachments on the assignment (handouts, slide decks, Google links).
        if a.attachments:
            await self._ingest_attachments(
                client=client, user_id=user_id, course_id=course_id,
                owner_external_id=external_id, attachments=a.attachments,
                google_token=google_token, report=report,
            )

    # ---- material ingestion: always via the login-scraper session ----
    async def _sync_scraped_materials(
        self, *, user_id: str, course_id: str, section: SchoologySection,
        scraper: SchoologyScraperClient, report: dict[str, Any],
        google_token: str | None = None, student_uid: str | None = None,
    ) -> None:
        """Materials via the login-scraper session (`schoology_scraper.py`) —
        the only materials path now (see the call site in `_sync_section`;
        the API's Courses-realm folder walk was retired because it's
        commonly blocked outright for a personal key, which surfaced as a
        noisy per-course auth error rather than a quiet empty list). Dedupes
        per course by item name, not a stable Schoology id: the scraped HTML
        doesn't expose one for a bare item, so `SchoologyScraperClient.
        walk_materials` is handed every name already recorded for this
        course and only returns what's new — a rescan that finds "a" and "b"
        where a prior scan already recorded "a" only needs to add "b" (per
        `walk_materials`'s `known_names`). `student_uid`, when known, also
        walks the app.schoology.com parent-preview URL — a parent account's
        real materials can live only there, not the district subdomain (see
        `walk_materials`'s docstring).

        When `section.id` is one of the confirmed-real courses
        (`course_mapping.KNOWN_SECTIONS`), walks that course's exact
        `materials_url` instead (`walk_known_url`) — no guessing across
        candidate URL shapes for a course whose real link is already on
        file.

        `scraper` is the caller's already-logged-in session, shared across
        every course in this sync run (see `sync()`/`_sync_materials_only()`)
        — this method neither logs in nor closes it."""
        try:
            existing = await supabase.select(
                "documents", columns="metadata",
                filters={"user_id": eq(user_id), "course_id": eq(course_id),
                         "external_source": eq(self.name)},
            ) or []
            known_names = {
                str((row.get("metadata") or {}).get("material_name") or "").strip().lower()
                for row in existing
            } - {""}

            materials_url = course_mapping.materials_url_for(section.id)
            if materials_url:
                items = await scraper.walk_known_url(materials_url, known_names=known_names)
            else:
                items = await scraper.walk_materials(
                    section.id, known_names=known_names, student_uid=student_uid,
                )
            for item in items:
                await self._ingest_scraped_material(
                    user_id=user_id, course_id=course_id, section=section,
                    item=item, scraper=scraper, google_token=google_token, report=report,
                )
        except SchoologyScraperAuthError as e:
            report["errors"].append(f"{section.display_name} materials (scrape login): {e}")
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{section.display_name} materials (scrape): {e}")

    # Schoology's own accessibility-text type prefixes that mean "this
    # item's href is (probably) a direct file download" — see
    # schoology_scraper.py's `_KNOWN_TYPE_PREFIXES`. Unconfirmed against a
    # real non-folder item yet, so `download_file`'s content-type check is
    # the real guard: if the href actually leads to an HTML detail page
    # instead, `_ingest_scraped_material` falls back to a reference rather
    # than trusting this label blindly.
    _SCRAPED_FILE_TYPES = frozenset({"file", "document"})

    async def _ingest_scraped_material(
        self, *, user_id: str, course_id: str, section: SchoologySection,
        item: MaterialLink, scraper: SchoologyScraperClient,
        google_token: str | None, report: dict[str, Any],
    ) -> None:
        """Download and record one new scraped item as real, searchable
        content — not just a link reference:
          - A Google Drive/Docs/Slides/Sheets link is downloaded through the
            existing Google Drive path (same as the API path's
            `_ingest_link`) when a Google token is available; without one
            it's recorded as a reference and flagged for auth, same
            fallback.
          - Anything Schoology itself labeled a "File"/"Document" (see
            `MaterialLink.material_type`) is fetched through the
            authenticated scraper session and ingested like any other
            document. If that GET actually returns an HTML page instead of
            a real file — the href led to an intermediate detail page, not
            a direct download — it falls back to a reference rather than
            ingesting the raw HTML or silently dropping the item.
          - Everything else (a Schoology page/discussion, a plain external
            link) is recorded as a searchable reference.
        `material_name` is always set in metadata, whichever branch is
        taken — `_sync_scraped_materials` reads it back to build the
        already-known set for the next scan's dedupe."""
        title = f"{item.folder_path + ' · ' if item.folder_path else ''}{item.name}"
        external_id = f"scrape:{section.id}:{_normalize_name(item.name)}"
        base_meta = {
            "material_name": item.name, "folder": item.folder_path or None,
            "material_type": item.material_type or None, "source_url": item.href,
        }

        if is_google_url(item.href):
            ref = parse_google_url(item.href)
            if ref and google_token:
                try:
                    content, filename, content_type = await download_google_file(
                        ref, google_token, name=title,
                    )
                    if await self._ingest_file(
                        user_id=user_id, course_id=course_id, external_id=external_id,
                        title=title, content=content, filename=filename,
                        content_type=content_type, doc_type="other",
                        extra_meta={**base_meta, "google_file_id": ref.file_id},
                    ):
                        report["documents"] += 1
                    return
                except Exception as e:  # noqa: BLE001
                    report["errors"].append(f"{section.display_name} · {item.name}: {e}")
                    # fall through to storing the link below
            elif ref:
                if await self._ingest_text(
                    user_id=user_id, course_id=course_id, external_id=external_id,
                    title=title, text=f"{title}\n{item.href}", doc_type="other",
                    extra_meta={**base_meta, "needs_google_auth": True},
                ):
                    report["documents"] += 1
                return

        elif (item.material_type or "").lower() in self._SCRAPED_FILE_TYPES:
            try:
                downloaded = await scraper.download_file(item.href)
            except Exception as e:  # noqa: BLE001
                downloaded = None
                report["errors"].append(f"{section.display_name} · {item.name}: {e}")
            if downloaded:
                content, content_type = downloaded
                if await self._ingest_file(
                    user_id=user_id, course_id=course_id, external_id=external_id,
                    title=title, content=content, filename=item.name,
                    content_type=content_type, doc_type="other", extra_meta=base_meta,
                ):
                    report["documents"] += 1
                return
            # Not actually a direct file (or the download failed) — fall
            # through to a plain reference below instead of dropping it.

        if await self._ingest_text(
            user_id=user_id, course_id=course_id, external_id=external_id,
            title=title, text=f"{title}\n{item.href}", doc_type="other", extra_meta=base_meta,
        ):
            report["documents"] += 1
