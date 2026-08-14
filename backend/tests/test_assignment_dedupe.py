"""`app.services.assignment_dedupe` -- possible-duplicate detection and the
merge/dismiss actions the assignments page's review section uses."""
from __future__ import annotations

import asyncio
import uuid

import app.services.assignment_dedupe as dedupe

USER_ID = str(uuid.uuid4())
COURSE_A = str(uuid.uuid4())
COURSE_B = str(uuid.uuid4())


def _assignment(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "title": "Lab Report 3",
        "course_id": COURSE_A,
        "due_date": "2026-03-10T00:00:00Z",
        "status": "not_started",
        "category": "lab",
        "external_source": "powerschool",
        "points_possible": None,
        "description": None,
        "notes": None,
        "weight": None,
        "difficulty": None,
        "estimated_minutes": None,
        "folder_id": None,
        "created_at": "2026-03-01T00:00:00Z",
    }
    base.update(overrides)
    return base


class _FakeSupabase:
    """Stands in for `app.core.supabase_client.supabase` -- records every
    call so tests can assert on exactly what was sent, without touching a
    real database."""

    def __init__(self, assignments=None, dismissals=None):
        self.assignments = list(assignments or [])
        self.dismissals = list(dismissals or [])
        self.updates: list[dict] = []
        self.deletes: list[dict] = []
        self.inserts: list[dict] = []

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        filters = filters or {}
        if table == "assignments":
            rows = self.assignments
            if "id" in filters and filters["id"].startswith("in."):
                ids = filters["id"][len("in.("):-1].split(",")
                rows = [a for a in rows if a["id"] in ids]
            return rows
        if table == "assignment_duplicate_dismissals":
            return self.dismissals
        return []

    async def update(self, table, patch, *, filters):
        self.updates.append({"table": table, "patch": patch, "filters": filters})
        return [patch]

    async def delete(self, table, *, filters):
        self.deletes.append({"table": table, "filters": filters})
        return None

    async def insert(self, table, rows, *, upsert=False, on_conflict=None):
        self.inserts.append({"table": table, "rows": rows, "upsert": upsert, "on_conflict": on_conflict})
        return [rows]


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(dedupe, "supabase", fake)
    return fake


# ---- find_possible_duplicates ------------------------------------------------

def test_flags_same_course_similar_title_and_close_due_date(monkeypatch):
    a = _assignment(title="Lab Report 3", external_source="powerschool")
    b = _assignment(title="Lab Report #3", external_source="schoology", id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    result = asyncio.run(dedupe.find_possible_duplicates(USER_ID))

    assert len(result) == 1
    assert {a["id"], b["id"]} == {x["id"] for x in result[0]["assignments"]}
    assert result[0]["suggested_keep_id"] in (a["id"], b["id"])


def test_does_not_flag_different_courses(monkeypatch):
    a = _assignment(course_id=COURSE_A)
    b = _assignment(course_id=COURSE_B, id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_does_not_flag_dissimilar_titles(monkeypatch):
    a = _assignment(title="Lab Report 3")
    b = _assignment(title="Chapter 7 Reading Quiz", id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_does_not_flag_similar_titles_with_far_apart_due_dates(monkeypatch):
    # "Homework 3" vs "Homework 4" style recurring-assignment trap -- a near
    # miss in wording must not be enough on its own when the due dates are
    # clearly different weeks.
    a = _assignment(title="Homework 3", due_date="2026-03-03T00:00:00Z")
    b = _assignment(title="Homework 3", due_date="2026-03-10T00:00:00Z", id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_missing_due_date_on_either_side_does_not_block_a_match(monkeypatch):
    a = _assignment(title="Lab Report 3", due_date=None)
    b = _assignment(title="Lab Report 3", due_date="2026-03-10T00:00:00Z", id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    assert len(asyncio.run(dedupe.find_possible_duplicates(USER_ID))) == 1


def test_matches_a_bare_date_against_a_timezone_aware_timestamp(monkeypatch):
    """Regression: PowerSchool's scraped due dates are bare, timezone-less
    dates ("2026-08-17") while Schoology's are timezone-aware timestamps
    ("...T00:00:00Z") -- comparing one of each used to raise TypeError
    ("can't subtract offset-naive and offset-aware datetimes") instead of
    just comparing the two dates, which would have crashed every single
    PowerSchoolProvider.sync() call that reached find_matching_assignment
    with any course that had a fetched assignment at all."""
    a = _assignment(title="Lab Report 3", due_date="2026-03-10")
    b = _assignment(title="Lab Report 3", due_date="2026-03-10T00:00:00Z", id=str(uuid.uuid4()))
    _install(monkeypatch, assignments=[a, b])

    assert len(asyncio.run(dedupe.find_possible_duplicates(USER_ID))) == 1


def test_excludes_pairs_already_dismissed(monkeypatch):
    a = _assignment(title="Lab Report 3")
    b = _assignment(title="Lab Report #3", id=str(uuid.uuid4()))
    pair = sorted([a["id"], b["id"]])
    _install(
        monkeypatch, assignments=[a, b],
        dismissals=[{"assignment_id_a": pair[0], "assignment_id_b": pair[1]}],
    )

    assert asyncio.run(dedupe.find_possible_duplicates(USER_ID)) == []


def test_suggests_keeping_the_row_with_more_filled_in_detail(monkeypatch):
    sparse = _assignment(title="Lab Report 3", points_possible=None, description=None)
    detailed = _assignment(
        title="Lab Report #3", id=str(uuid.uuid4()),
        points_possible=100, description="Full rubric here.",
    )
    _install(monkeypatch, assignments=[sparse, detailed])

    result = asyncio.run(dedupe.find_possible_duplicates(USER_ID))

    assert result[0]["suggested_keep_id"] == detailed["id"]
    assert result[0]["suggested_discard_id"] == sparse["id"]


# ---- find_matching_assignment --------------------------------------------------

def test_find_matching_assignment_returns_the_matching_existing_row():
    candidate = {"course_id": COURSE_A, "title": "U1: CK 27 - 29", "due_date": "2026-08-17"}
    existing = _assignment(title="U1: CK 27 - 29", due_date="2026-08-17T00:00:00Z")

    assert dedupe.find_matching_assignment(candidate, [existing]) == existing["id"]


def test_find_matching_assignment_returns_none_when_nothing_correlates():
    candidate = {"course_id": COURSE_A, "title": "Some New Thing", "due_date": "2026-08-17"}
    existing = _assignment(title="Completely Different Assignment", due_date="2026-08-17T00:00:00Z")

    assert dedupe.find_matching_assignment(candidate, [existing]) is None


def test_find_matching_assignment_ignores_a_different_course():
    candidate = {"course_id": COURSE_A, "title": "U1: CK 27 - 29", "due_date": "2026-08-17"}
    existing = _assignment(course_id=COURSE_B, title="U1: CK 27 - 29", due_date="2026-08-17T00:00:00Z")

    assert dedupe.find_matching_assignment(candidate, [existing]) is None


def test_find_matching_assignment_picks_the_best_of_several_candidates():
    candidate = {"course_id": COURSE_A, "title": "U1: CK 27 - 29", "due_date": "2026-08-17"}
    close_but_not_exact = _assignment(title="U1: CK 27 - 30", due_date="2026-08-17T00:00:00Z")
    exact = _assignment(title="U1: CK 27 - 29", due_date="2026-08-17T00:00:00Z")

    result = dedupe.find_matching_assignment(candidate, [close_but_not_exact, exact])

    assert result == exact["id"]


# ---- merge_assignments --------------------------------------------------------

def test_merge_reassigns_dependent_tables_and_fills_blank_fields(monkeypatch):
    keep = _assignment(points_possible=None, description=None)
    discard = _assignment(id=str(uuid.uuid4()), points_possible=100, description="Details from the other sync.")
    fake = _install(monkeypatch, assignments=[keep, discard])

    result = asyncio.run(dedupe.merge_assignments(USER_ID, keep["id"], discard["id"]))

    reassigned_tables = {u["table"] for u in fake.updates if u["patch"].get("assignment_id") == keep["id"]}
    assert reassigned_tables == set(dedupe._DEPENDENT_TABLES)
    for u in fake.updates:
        if u["patch"].get("assignment_id") == keep["id"]:
            assert u["filters"]["assignment_id"] == f"eq.{discard['id']}"

    fill_update = next(u for u in fake.updates if u["table"] == "assignments")
    assert fill_update["patch"]["points_possible"] == 100
    assert fill_update["patch"]["description"] == "Details from the other sync."

    assert fake.deletes == [{"table": "assignments", "filters": {"user_id": f"eq.{USER_ID}", "id": f"eq.{discard['id']}"}}]
    assert result["points_possible"] == 100


def test_merge_does_not_overwrite_a_field_the_kept_row_already_has(monkeypatch):
    keep = _assignment(points_possible=50)
    discard = _assignment(id=str(uuid.uuid4()), points_possible=100)
    fake = _install(monkeypatch, assignments=[keep, discard])

    asyncio.run(dedupe.merge_assignments(USER_ID, keep["id"], discard["id"]))

    fill_update = next((u for u in fake.updates if u["table"] == "assignments"), None)
    assert fill_update is None or "points_possible" not in fill_update["patch"]


def test_merge_rejects_merging_an_assignment_with_itself(monkeypatch):
    _install(monkeypatch)
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(dedupe.merge_assignments(USER_ID, "same-id", "same-id"))


def test_merge_raises_when_an_assignment_is_not_found(monkeypatch):
    keep = _assignment()
    _install(monkeypatch, assignments=[keep])
    import pytest
    with pytest.raises(LookupError):
        asyncio.run(dedupe.merge_assignments(USER_ID, keep["id"], str(uuid.uuid4())))


# ---- dismiss_duplicate ---------------------------------------------------------

def test_dismiss_stores_ids_in_canonical_sorted_order(monkeypatch):
    fake = _install(monkeypatch)
    high, low = "b-id", "a-id"

    asyncio.run(dedupe.dismiss_duplicate(USER_ID, high, low))

    assert fake.inserts[0]["rows"] == {
        "user_id": USER_ID, "assignment_id_a": "a-id", "assignment_id_b": "b-id",
    }
    assert fake.inserts[0]["upsert"] is True
