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
from app.integrations import course_mapping
from app.integrations.base import IntegrationProvider
from app.integrations.powerschool_browser import BrowserLoginError, login_and_get_cookie_header
from app.integrations.powerschool_client import (
    PSClass,
    PowerSchoolAuthError,
    PowerSchoolClient,
    UnsupportedLoginFlow,
    map_category,
    map_status,
)
from app.services import mistake_analysis


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

    async def _linked_course_id(self, user_id: str, ccid: str) -> str | None:
        """A course this provider already created/linked for this class --
        by its own `external_id`, or (if a different provider's row already
        claimed that column pair) by the `metadata.powerschool_ccid` link a
        prior sync left on it instead. Mirrors `SchoologyProvider.
        _resolve_course_id`'s equivalent two-step lookup."""
        existing = await supabase.select(
            "courses", columns="id",
            filters={"user_id": eq(user_id), "external_id": eq(ccid),
                     "external_source": eq(self.name)}, limit=1,
        )
        if existing:
            return existing[0]["id"]
        linked = await supabase.select(
            "courses", columns="id",
            filters={"user_id": eq(user_id), "metadata->>powerschool_ccid": eq(ccid)}, limit=1,
        )
        return linked[0]["id"] if linked else None

    async def _resolve_course_id(
        self, user_id: str, cls: PSClass, teacher_id: str | None,
        present_group_semesters: dict[str, set[str]],
    ) -> str:
        """Reuse an existing Schoology/manual/prior-PowerSchool course when
        one matches so the systems share a single course row instead of
        duplicating it -- mirrors `SchoologyProvider._resolve_course_id`.
        `course_mapping.resolve_grouped_course_id` handles the shared case
        of a class split across two differently-named semesters (e.g. "AP
        Biology" + "Bio PreLab HN", "AP Calculus AB" + "AP Calculus BC") --
        Schoology and PowerSchool both list each semester as its own
        section/class under the very same names, so the same group match
        lands both providers' data on the same two linked rows instead of
        each growing its own duplicate pair."""
        grouped = await course_mapping.resolve_grouped_course_id(
            provider_name=self.name, user_id=user_id, external_id=cls.ccid,
            display_name=cls.name, course_code=None,
            present_group_semesters=present_group_semesters,
            extra_fields={
                "room": cls.room or None, "period": cls.period, "teacher_id": teacher_id,
                "current_grade": cls.grade_percent, "current_letter": cls.grade_letter,
            },
            extra_meta={"powerschool_ccid": cls.ccid, "teacher": cls.teacher},
        )
        if grouped:
            return grouped

        # 1) A course this provider already created/linked for this class --
        #    just keep its grade current; scheduling fields were already
        #    filled (see step 2) the first time this row got linked.
        row_id = await self._linked_course_id(user_id, cls.ccid)
        if row_id:
            await supabase.update(
                "courses", {"current_grade": cls.grade_percent, "current_letter": cls.grade_letter},
                filters={"id": eq(row_id)},
            )
            return row_id

        # 2) An existing course (any source) whose name matches -- link,
        #    don't dupe (the reported "duplicate courses I didn't add" bug:
        #    a course a Schoology/manual sync already created, with its own
        #    documents/assignments, getting a second, empty PowerSchool-
        #    owned row instead of sharing the real one).
        all_courses = await supabase.select(
            "courses", columns="id,name,room,period,teacher_id,metadata",
            filters={"user_id": eq(user_id)},
        )
        for c in all_courses or []:
            if course_mapping.names_match(cls.name, c.get("name") or ""):
                meta = {**(c.get("metadata") or {}), "powerschool_ccid": cls.ccid, "teacher": cls.teacher}
                patch: dict[str, Any] = {
                    "metadata": meta, "current_grade": cls.grade_percent,
                    "current_letter": cls.grade_letter,
                }
                # Fill in scheduling details another provider didn't have, if empty.
                if cls.room and not c.get("room"):
                    patch["room"] = cls.room
                if cls.period and not c.get("period"):
                    patch["period"] = cls.period
                if teacher_id and not c.get("teacher_id"):
                    patch["teacher_id"] = teacher_id
                await supabase.update("courses", patch, filters={"id": eq(c["id"])})
                return c["id"]

        # 3) No match — create a PowerSchool-owned course.
        return await self.upsert_course(user_id, cls.ccid, {
            "name": cls.name, "period": cls.period, "room": cls.room or None,
            "teacher_id": teacher_id, "current_grade": cls.grade_percent,
            "current_letter": cls.grade_letter, "metadata": {"teacher": cls.teacher},
        }, create_only={"course_level": course_mapping.infer_course_level(cls.name)})

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

    async def debug_scrape_assignments(self, user_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetches one course's assignments detail page (`detail_href`) and
        reports its raw table structure — the per-course counterpart to
        `debug_scrape`, for diagnosing why a course's assignment-level
        grades aren't coming through even though its overall grade is.
        `query` narrows to the first course whose name contains it (e.g.
        `?q=AP+Calculus`); omitted, the first course with a detail link at
        all is used."""
        client = await self._authenticated_client(user_id)
        try:
            classes = await client.fetch_classes()
            if not classes:
                raise RuntimeError("No courses found on the grades page to scrape.")
            if query:
                q = query.strip().lower()
                cls = next((c for c in classes if q in c.name.lower()), None)
                if cls is None:
                    names = ", ".join(c.name for c in classes)
                    raise RuntimeError(f"No course matching {query!r} found. Courses: {names}")
            else:
                cls = next((c for c in classes if c.detail_href), classes[0])
            if not cls.detail_href:
                raise RuntimeError(f"{cls.name} has no assignments detail link to scrape.")
            return {
                "course": cls.name,
                "detail_href": cls.detail_href,
                **await client.debug_assignments_page(cls.detail_href),
            }
        finally:
            await client.aclose()

    async def sync(self, user_id: str, *, deadline: float | None = None) -> dict[str, Any]:
        # `deadline` isn't honored here — a PowerSchool account's course list
        # is small enough (one grades-page scrape) that chunking has never
        # been needed the way it is for Schoology's per-course materials
        # walk; see SchoologyProvider.sync.
        client = await self._authenticated_client(user_id)
        try:
            classes = await client.fetch_classes()
            courses = assignments_count = grades_count = excluded = 0
            errors: list[str] = []

            # Same "class split across two differently-named semesters"
            # evidence Schoology's sync computes (see
            # course_mapping.resolve_grouped_course_id) -- PowerSchool lists
            # each semester as its own class, under the same names, so a
            # student taking (e.g.) AP Biology this year sees both "Bio
            # PreLab HN" and "AP Biology" here too.
            present_group_semesters = course_mapping.compute_present_group_semesters(
                cls.name for cls in classes
            )

            for cls in classes:
                # Non-academic blocks (lunch, advisory) -- never imported as
                # a course, same exclusion Schoology applies (see
                # course_mapping.is_excluded): PowerSchool's own gradebook
                # lists these as ordinary classes too (e.g. "CAT Time",
                # "Lunch 1st Semester"). Any stale row a sync from before
                # this filter existed already created is removed too, same
                # self-heal Schoology's own exclusion does.
                if course_mapping.is_excluded(cls.name):
                    try:
                        await supabase.delete(
                            "courses",
                            filters={"user_id": eq(user_id), "external_id": eq(cls.ccid),
                                     "external_source": eq(self.name)},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    excluded += 1
                    continue

                teacher_id = await self._resolve_teacher_id(user_id, cls.teacher)
                try:
                    course_id = await self._resolve_course_id(
                        user_id, cls, teacher_id, present_group_semesters,
                    )
                except Exception as e:  # noqa: BLE001 — one course shouldn't sink the sync
                    errors.append(f"{cls.name}: {e}")
                    continue
                courses += 1

                # A manually-pasted scores.html URL (Settings/course page,
                # `courses.powerschool_url`) always wins over whatever this
                # sync scraped -- auto-detection can land on the wrong page
                # entirely (a real account's course-list link resolved to a
                # "Quick Links" widget instead of the real per-category
                # assignment tables), and there's no way to tell that apart
                # from a real assignments page short of a human confirming
                # the exact URL PowerSchool itself shows them for that course.
                override = await supabase.select(
                    "courses", columns="powerschool_url",
                    filters={"id": eq(course_id)}, limit=1,
                )
                override_url = (override[0].get("powerschool_url") if override else None) or None
                assignments_url = override_url or cls.detail_href
                if not assignments_url:
                    continue
                try:
                    assignments = await client.fetch_assignments(assignments_url)
                except PowerSchoolAuthError as e:
                    # A manually-pasted override can itself go stale (see
                    # fetch_assignments's docstring -- PowerSchool report
                    # links can be tied to the browser session they were
                    # copied from) even while this sync's own freshly-
                    # scraped `cls.detail_href` for the same course is still
                    # good. Fall back to it instead of dropping every
                    # assignment for the course sync after sync until a
                    # human happens to notice and re-paste the link.
                    if override_url and cls.detail_href and cls.detail_href != override_url:
                        try:
                            assignments = await client.fetch_assignments(cls.detail_href)
                            errors.append(
                                f"{cls.name}: the manually-pasted PowerSchool link needs to be "
                                f"re-copied ({e}) -- used the auto-detected link for this sync instead."
                            )
                        except Exception as e2:  # noqa: BLE001 — one course's markup shouldn't sink the sync
                            errors.append(f"{cls.name}: {e2}")
                            continue
                    else:
                        errors.append(f"{cls.name}: {e}")
                        continue
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
                        grade = await self.upsert_grade(user_id, assignment_id, course_id, {
                            "score": a.score,
                            "points_possible": a.points_possible,
                            "percentage": a.percentage,
                        })
                        grades_count += 1
                        if grade["changed"]:
                            # Best-effort -- a mistake-tracking hiccup must
                            # never sink the sync itself.
                            try:
                                await mistake_analysis.record_from_synced_grade(
                                    user_id, assignment_id=assignment_id, course_id=course_id,
                                    title=a.name, score=a.score,
                                    points_possible=a.points_possible, percentage=a.percentage,
                                )
                            except Exception:  # noqa: BLE001
                                pass

            return {
                "courses": courses, "assignments": assignments_count,
                "grades": grades_count, "excluded": excluded, "errors": errors,
            }
        finally:
            await client.aclose()
