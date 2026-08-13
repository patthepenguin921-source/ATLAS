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

    def __init__(self, classes: list[PSClass], assignments: dict[str, list[PSAssignment]] | None = None):
        self._classes = classes
        self._assignments = assignments or {}
        self.closed = False

    async def fetch_classes(self):
        return list(self._classes)

    async def fetch_assignments(self, href):
        return list(self._assignments.get(href, []))

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


def test_sync_does_not_split_a_standalone_course_with_no_lab_counterpart(fake_db, monkeypatch):
    """A stand-alone "AP Biology" with no "Bio PreLab HN"/"Biology ... HN"
    counterpart in this sync must not get needlessly split into a group."""
    provider = PowerSchoolProvider()
    report = _sync(provider, [_cls("1", "AP Biology")], monkeypatch)

    assert report["courses"] == 1
    c = fake_db.tables["courses"][0]
    assert c.get("linked_course_id") is None
    assert (c.get("metadata") or {}).get("course_group") is None
