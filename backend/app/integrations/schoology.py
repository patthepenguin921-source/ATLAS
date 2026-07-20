"""Schoology provider — imports courses, a week-at-a-glance schedule, and the
full contents of every course folder (assignments, files/slideshows, links,
pages, announcements) from the Schoology REST API, turning them into Atlas
knowledge.

Auth is two-legged OAuth 1.0a with a personal API key + secret the student
generates at ``<their-domain>/api`` — see ``schoology_client.py``. Credentials
are encrypted at rest in ``integrations.secret_ref`` (``app.core.crypto``),
mirroring the PowerSchool provider.

Deliberately does NOT touch grades: grading is owned by PowerSchool. This
provider matches each Schoology section to the student's existing (PowerSchool/
manual) course so the two systems share one course row instead of duplicating.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from app.core.crypto import decrypt_json, encrypt_json
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
    SchoologyMaterial,
    SchoologySection,
    files_of,
    links_of,
    week_bounds,
)
from app.services import ingestion

# Assignment/material title keywords → Atlas assignment_category enum values.
_CATEGORY_KEYWORDS = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation",
)


def encrypt_api_key(consumer_key: str, consumer_secret: str) -> str:
    return encrypt_json({"consumer_key": consumer_key, "consumer_secret": consumer_secret})


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
                "Schoology isn't connected yet — add your API key and secret first "
                "(generate them at your Schoology site's /api page)."
            )
        return rows[0]

    async def _client(self, integration: dict[str, Any]) -> SchoologyClient:
        creds = decrypt_json(integration["secret_ref"])
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

    async def _resolve_course(
        self, user_id: str, section: SchoologySection, present_group_semesters: dict[str, set[str]],
    ) -> str:
        """Return the course_id this Schoology section maps to, reusing an
        existing PowerSchool/manual/prior-Schoology course when one matches so
        the systems share a single course row. Only creates a new course when
        nothing matches."""
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
        True if newly ingested, False if it already existed (idempotent)."""
        if await self._document_exists(user_id, external_id):
            return False
        doc_id = str(uuid.uuid4())
        text = ""
        try:
            text = ingestion.extract_text(content, content_type, filename)
        except Exception:  # noqa: BLE001
            text = ""
        await supabase.insert("documents", {
            "id": doc_id, "user_id": user_id, "course_id": course_id,
            "title": title or filename or "Untitled", "doc_type": doc_type,
            "mime_type": content_type, "size_bytes": len(content),
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
        config = integration.get("config") or {}
        google_token = config.get("google_access_token")  # optional, for Drive downloads
        client = await self._client(integration)
        report: dict[str, Any] = {
            "courses": 0, "clubs": 0, "excluded": 0, "assignments": 0, "events": 0,
            "documents": 0, "links": 0, "announcements": 0, "errors": [],
        }
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

                await self._sync_section(
                    client=client, user_id=user_id, course_id=course_id, section=section,
                    monday=monday, sunday=sunday, google_token=google_token, report=report,
                )

            return report
        finally:
            await client.aclose()

    async def _sync_section(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        section: SchoologySection, monday: date, sunday: date,
        google_token: str | None, report: dict[str, Any],
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
        #    searchable course knowledge.
        try:
            materials = await client.walk_materials(sid)
        except Exception as e:  # noqa: BLE001
            materials = []
            report["errors"].append(f"{section.display_name} materials: {e}")
        for m in materials:
            await self._import_material(
                client=client, user_id=user_id, course_id=course_id,
                m=m, google_token=google_token, report=report,
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

    async def _import_material(
        self, *, client: SchoologyClient, user_id: str, course_id: str,
        m: SchoologyMaterial, google_token: str | None, report: dict[str, Any],
    ) -> None:
        owner_external_id = f"material:{m.id}"
        # Fetch the material's full record for attachments the folder listing omits.
        attachments = m.attachments
        if not attachments and m.location and m.type in ("document", "assignment", "page", "discussion"):
            try:
                detail = await client.fetch_material_detail(m)
                attachments = detail.get("attachments") or {}
            except Exception:  # noqa: BLE001
                attachments = {}

        title = f"{m.folder_path + ' · ' if m.folder_path else ''}{m.title}"

        # A page/discussion body is itself content worth indexing.
        if m.body:
            if await self._ingest_text(
                user_id=user_id, course_id=course_id, external_id=f"{owner_external_id}:body",
                title=title, text=m.body, doc_type="other",
                extra_meta={"schoology_type": m.type, "folder": m.folder_path},
            ):
                report["documents"] += 1

        # A bare external URL on the item (e.g. a link-type material).
        if m.url:
            await self._ingest_link(
                client=client, user_id=user_id, course_id=course_id,
                external_id=f"{owner_external_id}:url", title=title, url=m.url,
                google_token=google_token, report=report,
            )

        if attachments:
            await self._ingest_attachments(
                client=client, user_id=user_id, course_id=course_id,
                owner_external_id=owner_external_id, attachments=attachments,
                google_token=google_token, report=report,
            )
