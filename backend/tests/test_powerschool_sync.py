"""PowerSchoolProvider.sync's course reconciliation -- reusing an existing
Schoology/manual/prior-PowerSchool course row (by name match or by a shared
course group, see `course_mapping.resolve_grouped_course_id`) instead of
growing a second, duplicate, empty course row every time PowerSchool syncs.
The scraping mechanics themselves are covered in test_powerschool.py; this
exercises `sync()` end-to-end against an in-memory Supabase and a fake
PowerSchoolClient.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

import app.integrations.powerschool as powerschool_module
from app.config import settings
from app.core.supabase_client import supabase
from app.integrations.powerschool import PowerSchoolProvider
from app.integrations.powerschool_client import PSAssignment, PSClass

USER_ID = str(uuid.uuid4())


class FakeSupabase:
    def __init__(self, courses: list[dict[str, Any]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "courses": courses or [], "assignments": [], "grades": [], "teachers": [],
        }

    @staticmethod
    def _match(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if "->>" in k:
                col, prop = k.split("->>", 1)
                got = (row.get(col) or {}).get(prop)
            else:
                got = row.get(k)
            if str(got) != str(want):
                return False
        return True

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        rows = [r for r in self.tables.setdefault(table, []) if self._match(r, filters)]
        return rows[:limit] if limit else rows

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        rows = [rows] if isinstance(rows, dict) else rows
        out = []
        for r in rows:
            row = dict(r)
            row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)
            out.append(row)
        return out

    async def update(self, table, patch, *, filters):
        out = []
        for row in self.tables.setdefault(table, []):
            if self._match(row, filters):
                row.update(patch)
                out.append(row)
        return out

    async def delete(self, table, *, filters):
        keep, removed = [], []
        for row in self.tables.setdefault(table, []):
            (removed if self._match(row, filters) else keep).append(row)
        self.tables[table] = keep
        return removed


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase()
    for name in ("select", "insert", "update", "delete"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    return fake


class _FakeClient:
    """Stands in for `PowerSchoolClient` — `PowerSchoolProvider._authenticated_
    client` is monkeypatched to hand this back directly, same as the real one
    past login."""

    base_url = "https://fake.powerschool.com"

    def __init__(self, classes: list[PSClass], assignments: dict[str, list[PSAssignment]] | None = None):
        self._classes = classes
        self._assignments = assignments or {}
        self.closed = False

    async def fetch_classes(self):
        return list(self._classes)

    async def fetch_assignments(self, href):
        result = self._assignments.get(href, [])
        if isinstance(result, BaseException):
            raise result
        return list(result)

    def cookie_header(self):
        return "sessionid=fake"

    async def aclose(self):
        self.closed = True


def _cls(ccid, name, *, period="1", room="A1", teacher="Ms. Rivera",
         grade_percent=None, grade_letter=None, detail_href=None) -> PSClass:
    return PSClass(
        ccid=ccid, period=period, name=name, teacher=teacher, room=room,
        grade_letter=grade_letter, grade_percent=grade_percent, detail_href=detail_href,
    )


def _sync(provider, classes, monkeypatch, assignments=None):
    async def _fake_authenticated_client(self, user_id):
        return _FakeClient(classes, assignments)

    monkeypatch.setattr(PowerSchoolProvider, "_authenticated_client", _fake_authenticated_client)
    return asyncio.run(provider.sync(USER_ID))


def test_sync_creates_a_new_course_when_nothing_matches(fake_db, monkeypatch):
    provider = PowerSchoolProvider()
    report = _sync(provider, [_cls("100", "Algebra II", grade_percent=91.0, grade_letter="A-")], monkeypatch)

    assert report["courses"] == 1
    courses = fake_db.tables["courses"]
    assert len(courses) == 1
    c = courses[0]
    assert c["name"] == "Algebra II"
    assert c["external_source"] == "powerschool" and c["external_id"] == "100"
    assert c["current_grade"] == 91.0 and c["current_letter"] == "A-"
    assert c["course_level"] == "regular"


def test_sync_links_onto_an_existing_course_by_name_instead_of_duplicating(fake_db, monkeypatch):
    """The reported bug: a course a Schoology/manual sync already created
    (with its own documents) got a second, empty PowerSchool-owned row
    instead of sharing the real one."""
    existing_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": existing_id, "user_id": USER_ID, "name": "AP English Lang",
        "external_source": "schoology", "external_id": "sec-1", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817354", "AP English Lang", period="4-6(A-E)", room="L D107", grade_percent=88.0, grade_letter="B+"),
    ], monkeypatch)

    assert report["courses"] == 1
    courses = fake_db.tables["courses"]
    assert len(courses) == 1  # linked in place, not duplicated
    c = courses[0]
    assert c["id"] == existing_id
    assert c["external_source"] == "schoology"  # ownership untouched
    assert c["metadata"]["powerschool_ccid"] == "8817354"
    assert c["current_grade"] == 88.0 and c["current_letter"] == "B+"
    assert c["room"] == "L D107" and c["period"] == "4-6(A-E)"  # filled in, was empty


def test_sync_does_not_overwrite_an_already_filled_scheduling_field(fake_db, monkeypatch):
    existing_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": existing_id, "user_id": USER_ID, "name": "AP English Lang",
        "external_source": "manual", "external_id": None, "metadata": {},
        "room": "Room already set", "period": None, "teacher_id": None,
    })

    provider = PowerSchoolProvider()
    _sync(provider, [_cls("8817354", "AP English Lang", room="L D107")], monkeypatch)

    assert fake_db.tables["courses"][0]["room"] == "Room already set"


def test_sync_reuses_the_linked_course_on_a_later_sync_without_duplicating(fake_db, monkeypatch):
    provider = PowerSchoolProvider()
    _sync(provider, [_cls("100", "Algebra II", grade_percent=80.0, grade_letter="B-")], monkeypatch)
    _sync(provider, [_cls("100", "Algebra II", grade_percent=85.5, grade_letter="B")], monkeypatch)

    courses = fake_db.tables["courses"]
    assert len(courses) == 1
    assert courses[0]["current_grade"] == 85.5 and courses[0]["current_letter"] == "B"


def test_sync_excludes_lunch_and_cat_time_and_removes_stale_rows(fake_db, monkeypatch):
    # A stale row from before this filter existed.
    fake_db.tables["courses"].append({
        "id": str(uuid.uuid4()), "user_id": USER_ID, "name": "CAT Time",
        "external_source": "powerschool", "external_id": "999", "metadata": {},
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("999", "CAT Time"), _cls("998", "Lunch 1st Semester"),
        _cls("100", "Algebra II"),
    ], monkeypatch)

    assert report["excluded"] == 2
    assert report["courses"] == 1
    names = {c["name"] for c in fake_db.tables["courses"]}
    assert names == {"Algebra II"}


def test_sync_merges_biology_prep_lab_and_ap_onto_existing_schoology_group_rows(fake_db, monkeypatch):
    """AP Biology + Bio PreLab HN must land PowerSchool's grades on the very
    same two linked rows Schoology's sync already created/grouped, not grow
    a second, ungrouped pair."""
    s1_id, s2_id = str(uuid.uuid4()), str(uuid.uuid4())
    fake_db.tables["courses"] += [
        {
            "id": s1_id, "user_id": USER_ID, "name": "AP Biology",
            "external_source": "schoology", "external_id": "sec-lab",
            "semester": "s1", "course_level": "honors", "linked_course_id": None,
            "has_hn_prep_lab": True, "has_ap_prep_lab": False,
            "metadata": {"course_group": "ap_biology", "schoology_section_id": "sec-lab"},
            "room": None, "period": None, "teacher_id": None,
        },
        {
            "id": s2_id, "user_id": USER_ID, "name": "AP Biology",
            "external_source": "schoology", "external_id": "sec-ap",
            "semester": "s2", "course_level": "ap", "linked_course_id": s1_id,
            "has_hn_prep_lab": False, "has_ap_prep_lab": True,
            "metadata": {"course_group": "ap_biology", "schoology_section_id": "sec-ap"},
            "room": None, "period": None, "teacher_id": None,
        },
    ]

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817356", "Bio PreLab HN", period="8-14(A-E)", room="L A206", grade_percent=94.0, grade_letter="A"),
        _cls("8817355", "AP Biology", period="8-14(A-E)", room="L A206", grade_percent=None, grade_letter=None),
    ], monkeypatch)

    assert report["courses"] == 2
    courses = fake_db.tables["courses"]
    assert len(courses) == 2  # still just the two grouped rows -- no duplicates grown
    by_id = {c["id"]: c for c in courses}

    lab = by_id[s1_id]
    assert lab["current_grade"] == 94.0 and lab["current_letter"] == "A"
    assert lab["external_source"] == "powerschool" and lab["external_id"] == "8817356"
    assert lab["metadata"]["powerschool_ccid"] == "8817356"
    assert lab["metadata"]["course_group"] == "ap_biology"  # group identity preserved
    assert lab["room"] == "L A206"

    ap = by_id[s2_id]
    assert ap["external_source"] == "powerschool" and ap["external_id"] == "8817355"
    assert ap["linked_course_id"] == s1_id  # still linked to the same root


def test_sync_attaches_grade_to_an_existing_assignment_instead_of_duplicating(fake_db, monkeypatch):
    """Assignments are meant to come from Schoology (or manual entry) --
    when a PowerSchool-scraped assignment looks like the same real
    assignment as one that already exists in the course, PowerSchool must
    attach its grade to that existing row instead of creating a redundant
    second one under its own external_id."""
    course_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": course_id, "user_id": USER_ID, "name": "AP Calculus AB",
        "external_source": "powerschool", "external_id": "8817372", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
    })
    existing_assignment_id = str(uuid.uuid4())
    fake_db.tables["assignments"].append({
        "id": existing_assignment_id, "user_id": USER_ID, "course_id": course_id,
        "title": "U1: CK 27 - 29", "due_date": "2026-08-17", "external_source": "schoology",
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=1"),
    ], monkeypatch, assignments={
        "/guardian/scores.html?frn=1": [
            PSAssignment(name="U1: CK 27 - 29", category="Homework", due_date="2026-08-17",
                         score=100.0, points_possible=100.0, percentage=100.0),
        ],
    })

    assert report["courses"] == 1
    assert report["assignments"] == 1
    # No new assignment row -- the grade landed on the existing (Schoology) one.
    assert len(fake_db.tables["assignments"]) == 1
    assert fake_db.tables["assignments"][0]["id"] == existing_assignment_id
    grades = fake_db.tables["grades"]
    assert len(grades) == 1
    assert grades[0]["assignment_id"] == existing_assignment_id
    assert grades[0]["score"] == 100.0


def test_sync_creates_a_new_assignment_when_nothing_correlates(fake_db, monkeypatch):
    """A course with no matching existing assignment (no Schoology
    connection, nothing entered manually) still gets a real PowerSchool-
    owned assignment + grade created, same as before this correlation
    behavior existed."""
    course_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": course_id, "user_id": USER_ID, "name": "AP Calculus AB",
        "external_source": "powerschool", "external_id": "8817372", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=1"),
    ], monkeypatch, assignments={
        "/guardian/scores.html?frn=1": [
            PSAssignment(name="U1: CK 27 - 29", category="Homework", due_date="2026-08-17",
                         score=100.0, points_possible=100.0, percentage=100.0),
        ],
    })

    assert report["courses"] == 1
    assert report["assignments"] == 1
    assert len(fake_db.tables["assignments"]) == 1
    new_row = fake_db.tables["assignments"][0]
    assert new_row["external_source"] == "powerschool"
    assert fake_db.tables["grades"][0]["assignment_id"] == new_row["id"]


def test_sync_does_not_match_onto_its_own_previously_synced_assignment(fake_db, monkeypatch):
    """A PowerSchool-owned row from a prior sync must never be treated as a
    correlation candidate for a later sync of that same item -- it's
    already found (and its due_date/category/status kept current) via
    `upsert_assignment`'s own external_id lookup; matching onto it here
    instead would silently stop that update from ever running again."""
    course_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": course_id, "user_id": USER_ID, "name": "AP Calculus AB",
        "external_source": "powerschool", "external_id": "8817372", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
    })
    prior_powerschool_id = str(uuid.uuid4())
    fake_db.tables["assignments"].append({
        "id": prior_powerschool_id, "user_id": USER_ID, "course_id": course_id,
        "title": "U1: CK 27 - 29", "due_date": "2026-08-17", "external_source": "powerschool",
        "external_id": "8817372:U1: CK 27 - 29:2026-08-17",
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=1"),
    ], monkeypatch, assignments={
        "/guardian/scores.html?frn=1": [
            PSAssignment(name="U1: CK 27 - 29", category="Homework", due_date="2026-08-17",
                         score=100.0, points_possible=100.0, percentage=100.0),
        ],
    })

    assert report["courses"] == 1
    assert report["assignments"] == 1
    # Still just the one (reused, not duplicated) row -- upsert_assignment's
    # own external_id match found it, not the correlation search.
    assert len(fake_db.tables["assignments"]) == 1
    assert fake_db.tables["assignments"][0]["id"] == prior_powerschool_id
    assert fake_db.tables["grades"][0]["assignment_id"] == prior_powerschool_id


def test_sync_prefers_a_manually_set_powerschool_url_over_the_scraped_detail_href(fake_db, monkeypatch):
    """A course's `powerschool_url` (pasted by the student in Settings/the
    course page) always wins over whatever detail_href the sync itself
    scraped -- auto-detection can land on the wrong page entirely (a real
    account's course-list link resolved to an unrelated "Quick Links"
    widget instead of the real per-category assignment tables), and a
    student confirming the exact scores.html URL PowerSchool shows them is
    the reliable escape hatch for that."""
    existing_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": existing_id, "user_id": USER_ID, "name": "AP Calculus AB",
        "external_source": "powerschool", "external_id": "8817372", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
        "powerschool_url": "https://lexington1.powerschool.com/guardian/scores.html?frn=00437309537",
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=wrong-page"),
    ], monkeypatch, assignments={
        "https://lexington1.powerschool.com/guardian/scores.html?frn=00437309537": [
            PSAssignment(name="Real Quiz", category="Quiz", due_date="2026-08-20",
                         score=9.0, points_possible=10.0, percentage=90.0),
        ],
        "/guardian/scores.html?frn=wrong-page": [
            PSAssignment(name="School Fees and Forms", category="other", due_date=None,
                         score=None, points_possible=None, percentage=None),
        ],
    })

    assert report["courses"] == 1
    assert report["assignments"] == 1
    titles = {a["title"] for a in fake_db.tables["assignments"]}
    assert titles == {"Real Quiz"}


def test_sync_falls_back_to_the_scraped_link_when_the_manual_override_is_stale(fake_db, monkeypatch):
    """A `powerschool_url` override can itself go stale (e.g. it embeds a
    session-scoped token from the browser session it was pasted from) even
    while this sync's own freshly-scraped `detail_href` for the same course
    still works fine. Regression: this used to drop every assignment for
    the course, silently and forever, since the override always won and a
    stale link raising `PowerSchoolAuthError` was treated the same as any
    other unrecoverable per-course failure."""
    from app.integrations.powerschool_client import PowerSchoolAuthError

    existing_id = str(uuid.uuid4())
    fake_db.tables["courses"].append({
        "id": existing_id, "user_id": USER_ID, "name": "AP Calculus AB",
        "external_source": "powerschool", "external_id": "8817372", "metadata": {},
        "room": None, "period": None, "teacher_id": None,
        "powerschool_url": "https://lexington1.powerschool.com/guardian/scores.html?frn=stale",
    })

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=fresh"),
    ], monkeypatch, assignments={
        "https://lexington1.powerschool.com/guardian/scores.html?frn=stale": PowerSchoolAuthError(
            "Got PowerSchool's sign-in page instead of the assignments page."
        ),
        "/guardian/scores.html?frn=fresh": [
            PSAssignment(name="Real Quiz", category="Quiz", due_date="2026-08-20",
                         score=9.0, points_possible=10.0, percentage=90.0),
        ],
    })

    assert report["courses"] == 1
    assert report["assignments"] == 1
    titles = {a["title"] for a in fake_db.tables["assignments"]}
    assert titles == {"Real Quiz"}
    assert len(report["errors"]) == 1
    assert "re-copied" in report["errors"][0]


class _FakeBrowserFetcher:
    """Stands in for `RenderedAssignmentsFetcher` -- the real one drives an
    actual headless Chromium (see test_powerschool_browser.py for that),
    which this fast, mocked-Supabase suite doesn't want to pay for. This
    only exercises that `sync()` calls it with the right arguments when the
    plain HTTP fetch comes back empty, and uses whatever it returns."""

    instances: list["_FakeBrowserFetcher"] = []

    def __init__(self, base_url, cookie_header):
        self.base_url = base_url
        self.cookie_header_value = cookie_header
        self.fetched_urls: list[str] = []
        self.closed = False
        _FakeBrowserFetcher.instances.append(self)

    async def fetch_rendered_html(self, url):
        self.fetched_urls.append(url)
        return "<html>rendered</html>"

    async def aclose(self):
        self.closed = True


def test_sync_falls_back_to_a_real_browser_when_the_plain_fetch_finds_nothing(fake_db, monkeypatch):
    """Some PowerSchool skins (confirmed against a real Lexington1 account)
    fill the Assignments grid in via client-side JS well after the initial
    page load -- a plain HTTP GET's response never contains it, immediately
    or after any delay (see debug_assignments_page's "fetch twice" check).
    When the plain fetch comes back empty, `sync()` must fall back to a real
    headless browser rendering the same URL rather than treating that
    silently as "this course has no assignments."."""
    _FakeBrowserFetcher.instances = []
    monkeypatch.setattr(powerschool_module, "RenderedAssignmentsFetcher", _FakeBrowserFetcher)
    monkeypatch.setattr(
        powerschool_module, "parse_assignments_html",
        lambda html: [
            PSAssignment(name="Limits Quiz", category="Quiz", due_date="2026-08-13",
                         score=81.0, points_possible=100.0, percentage=81.0),
        ] if html == "<html>rendered</html>" else [],
    )
    monkeypatch.setattr(settings, "vercel", "")  # not serverless -- browser fallback allowed

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=1"),
    ], monkeypatch, assignments={"/guardian/scores.html?frn=1": []})

    assert report["courses"] == 1
    assert report["assignments"] == 1
    titles = {a["title"] for a in fake_db.tables["assignments"]}
    assert titles == {"Limits Quiz"}
    assert len(_FakeBrowserFetcher.instances) == 1
    assert _FakeBrowserFetcher.instances[0].fetched_urls == ["/guardian/scores.html?frn=1"]
    assert _FakeBrowserFetcher.instances[0].closed is True


def test_sync_skips_the_browser_fallback_on_serverless_hosting(fake_db, monkeypatch):
    """No Chromium binary/execution budget on Vercel -- same gate the
    existing CAS-login browser fallback already uses (see
    `_authenticated_client`). A course with genuinely no assignments must
    stay that way rather than erroring, and the browser fetcher must never
    even be constructed."""
    _FakeBrowserFetcher.instances = []
    monkeypatch.setattr(powerschool_module, "RenderedAssignmentsFetcher", _FakeBrowserFetcher)
    monkeypatch.setattr(settings, "vercel", "1")  # serverless -- browser fallback must not run

    provider = PowerSchoolProvider()
    report = _sync(provider, [
        _cls("8817372", "AP Calculus AB", detail_href="/guardian/scores.html?frn=1"),
    ], monkeypatch, assignments={"/guardian/scores.html?frn=1": []})

    assert report["courses"] == 1
    assert report["assignments"] == 0
    assert _FakeBrowserFetcher.instances == []


def test_sync_does_not_split_a_standalone_course_with_no_lab_counterpart(fake_db, monkeypatch):
    """A stand-alone "AP Biology" with no "Bio PreLab HN"/"Biology ... HN"
    counterpart in this sync must not get needlessly split into a group."""
    provider = PowerSchoolProvider()
    report = _sync(provider, [_cls("1", "AP Biology")], monkeypatch)

    assert report["courses"] == 1
    c = fake_db.tables["courses"][0]
    assert c.get("linked_course_id") is None
    assert (c.get("metadata") or {}).get("course_group") is None
