"""Schoology provider + client, exercised against a fake Schoology REST API
(httpx.MockTransport) and an in-memory Supabase — no live credentials needed.

The live API's real request/response shapes were captured while building this
(OAuth two-legged signing, the `{"section": [...]}` / `{"assignment": [...]}`
envelopes, recursive `folder-item` listings, and `attachments.files.file[]` /
`attachments.links.link[]`); these fixtures mirror them.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
import pytest

from app.core.supabase_client import supabase
from app.integrations import google_files
from app.integrations.schoology import (
    SchoologyProvider,
    _map_category,
    _names_match,
    _normalize_name,
)
from app.integrations.schoology_client import (
    SchoologyClient,
    files_of,
    links_of,
    week_bounds,
)
from app.services import ingestion

USER_ID = str(uuid.uuid4())
BIO_COURSE = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Fake Schoology REST API
# ---------------------------------------------------------------------------
def _this_week_iso() -> str:
    monday, _ = week_bounds()
    return datetime.combine(monday + timedelta(days=1), datetime.min.time()).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


SECTION_ID = "555"

SECTIONS = {"section": [{
    "id": SECTION_ID, "course_title": "AP Biology", "section_title": "Sec 1",
    "course_code": "APBIO", "section_code": "", "grading_periods": [1],
    "meeting_days": ["1", "3"], "start_time": "08:00", "end_time": "08:50",
    "location": "F207", "active": 1,
    "profile_url": "https://lexington1.schoology.com/course/555",
}]}

ASSIGNMENTS = {"assignment": [{
    "id": "9001", "title": "Cell Respiration Lab", "description": "Do the lab.",
    "due": _this_week_iso(), "max_points": "100", "assignment_type": "assignment",
    "type": "assignment", "web_url": "https://app.schoology.com/assignments/9001/info",
    "folder_id": "42", "published": 1,
    "attachments": {
        "files": {"file": [{
            "id": "7", "title": "Lab handout", "filename": "lab.pdf",
            "download_path": "https://api.schoology.com/v1/download/7", "extension": "pdf",
        }]},
        "links": {"link": [{
            "id": "8", "title": "Slides",
            "url": "https://docs.google.com/presentation/d/ABC123/edit",
        }]},
    },
}]}

EVENTS = {"event": [
    {"id": "3001", "title": "Unit 1 Exam", "description": "Chapters 1-3",
     "start": _this_week_iso(), "has_end": 0, "end": "", "all_day": 0,
     "type": "event", "assignment_id": None,
     "web_url": "https://app.schoology.com/event/3001"},
    # An assignment-linked event should be skipped (deduped by the assignment).
    {"id": "3002", "title": "Cell Respiration Lab", "start": _this_week_iso(),
     "type": "assignment", "assignment_id": "9001", "web_url": ""},
    # An event far in the past must be filtered out of the week view.
    {"id": "3003", "title": "Old thing", "start": "2020-01-01 09:00:00",
     "type": "event", "assignment_id": None, "web_url": ""},
]}

# Root folder -> a subfolder -> a document with a file attachment, plus a bare
# file dropped straight into the folder (no "type"/"location"/"attachments" in
# the listing itself — only resolvable via the Documents resource).
FOLDER_ROOT = {"folder-item": [
    {"id": "42", "type": "folder", "title": "Unit 1"},
]}
FOLDER_42 = {"folder-item": [
    {"id": "77", "type": "document", "title": "Intro Notes",
     "location": "https://api.schoology.com/v1/sections/555/documents/77"},
    {"id": "99", "title": "syllabus.pdf"},
]}
DOCUMENT_77 = {
    "id": "77", "title": "Intro Notes", "type": "document",
    "attachments": {"files": {"file": [{
        "id": "88", "title": "Notes", "filename": "notes.pdf",
        "download_path": "https://api.schoology.com/v1/download/88", "extension": "pdf",
    }]}},
}
DOCUMENT_99 = {
    "id": "99", "title": "syllabus.pdf",
    "attachments": {"files": {"file": [{
        "id": "100", "title": "syllabus.pdf", "filename": "syllabus.pdf",
        "download_path": "https://api.schoology.com/v1/download/100", "extension": "pdf",
    }]}},
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/app-user-info":
        return httpx.Response(200, json={"api_uid": 12345678})
    if path.endswith("/sections") and "/users/" in path:
        return httpx.Response(200, json=SECTIONS)
    if path.endswith(f"/sections/{SECTION_ID}/assignments"):
        return httpx.Response(200, json=ASSIGNMENTS)
    if path.endswith(f"/sections/{SECTION_ID}/events"):
        return httpx.Response(200, json=EVENTS)
    if path.endswith(f"/sections/{SECTION_ID}/folder/0"):
        return httpx.Response(200, json=FOLDER_ROOT)
    if path.endswith(f"/sections/{SECTION_ID}/folder/42"):
        return httpx.Response(200, json=FOLDER_42)
    if path.endswith("/documents/77"):
        return httpx.Response(200, json=DOCUMENT_77)
    if path.endswith("/documents/99"):
        return httpx.Response(200, json=DOCUMENT_99)
    if "/download/" in path:
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes")
    return httpx.Response(404, json={"error": f"no fixture for {path}"})


def _mock_client() -> SchoologyClient:
    return SchoologyClient("ckey", "csecret", transport=httpx.MockTransport(_handler))


# ---------------------------------------------------------------------------
# In-memory Supabase
# ---------------------------------------------------------------------------
class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "courses": [{
                "id": BIO_COURSE, "user_id": USER_ID, "name": "AP Biology",
                "code": "BIO", "external_source": "powerschool", "external_id": "ps-bio",
                "metadata": {},
            }],
            "assignments": [], "grades": [], "calendar_events": [], "documents": [],
            "document_chunks": [],
        }

    @staticmethod
    def _match(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if "->>" in k:  # JSON path filter, e.g. metadata->>schoology_section_id
                col, prop = k.split("->>", 1)
                got = (row.get(col) or {}).get(prop)
            else:
                got = row.get(k)
            if str(got) != str(want):
                return False
        return True

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [r for r in self.tables.setdefault(table, []) if self._match(r, filters)]

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
    # Skip real text-extract/embed; we only assert the plumbing here.
    monkeypatch.setattr(ingestion, "extract_text", lambda content, mt, fn="": "extracted text")

    async def _noop_ingest(document_id, user_id, text):
        return {"chunks": 1}

    monkeypatch.setattr(ingestion, "ingest_document", _noop_ingest)
    return fake


# ---------------------------------------------------------------------------
# OAuth signing
# ---------------------------------------------------------------------------
def _expected_sig(secret: str, method: str, base_url: str, params: dict[str, str]) -> str:
    def q(s):
        return quote(str(s), safe="~")

    param_str = "&".join(f"{q(k)}={q(v)}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), q(base_url), q(param_str)])
    return base64.b64encode(hmac.new(f"{secret}&".encode(), base.encode(), hashlib.sha1).digest()).decode()


def _parse_oauth_header(header: str) -> dict[str, str]:
    assert header.startswith("OAuth ")
    out = {}
    for part in header[len("OAuth "):].split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def test_oauth_header_folds_in_query_params():
    """Query params MUST be part of the signature base string — omitting them
    is what produced live 401 'signature failed' errors."""
    client = SchoologyClient("ckey", "csecret")
    url = "https://api.schoology.com/v1/sections/555/assignments?with_attachments=1&limit=200"
    header = client._auth_header("GET", url)
    parsed = _parse_oauth_header(header)

    assert parsed["realm"] == "Schoology API"
    assert parsed["oauth_consumer_key"] == "ckey"
    assert parsed["oauth_signature_method"] == "HMAC-SHA1"

    signed_params = {k: v for k, v in parsed.items() if k.startswith("oauth_") and k != "oauth_signature"}
    signed_params.update({"with_attachments": "1", "limit": "200"})
    expected = _expected_sig(
        "csecret", "GET", "https://api.schoology.com/v1/sections/555/assignments", signed_params
    )
    # The percent-decoded signature in the header must match one computed WITH
    # the query params included.
    from urllib.parse import unquote

    assert unquote(parsed["oauth_signature"]) == expected


def test_oauth_nonce_is_per_request():
    client = SchoologyClient("ckey", "csecret")
    h1 = _parse_oauth_header(client._auth_header("GET", "https://api.schoology.com/v1/x"))
    h2 = _parse_oauth_header(client._auth_header("GET", "https://api.schoology.com/v1/x"))
    assert h1["oauth_nonce"] != h2["oauth_nonce"]


# ---------------------------------------------------------------------------
# Client parsing
# ---------------------------------------------------------------------------
def test_client_parses_sections_assignments_events():
    async def run():
        client = _mock_client()
        try:
            uid = await client.current_user_id()
            assert uid == "12345678"
            sections = await client.get_sections(uid)
            assert len(sections) == 1 and sections[0].display_name == "AP Biology"

            assignments = await client.get_assignments(SECTION_ID)
            assert len(assignments) == 1
            a = assignments[0]
            assert a.title == "Cell Respiration Lab" and a.max_points == 100.0
            assert files_of(a.attachments)[0]["filename"] == "lab.pdf"
            assert "docs.google.com" in links_of(a.attachments)[0]["url"]

            events = await client.get_events(SECTION_ID)
            assert {e.id for e in events} == {"3001", "3002", "3003"}
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_materials_recurses_into_subfolders():
    async def run():
        client = _mock_client()
        try:
            materials = await client.walk_materials(SECTION_ID)
            # Both the document with an explicit location AND the bare file
            # (no type/location/attachments in the listing) nested inside
            # "Unit 1" must be found, with breadcrumb.
            assert len(materials) == 2
            by_title = {m.title: m for m in materials}
            notes = by_title["Intro Notes"]
            assert notes.folder_path == "Unit 1"
            detail = await client.fetch_material_detail(notes)
            assert files_of(detail["attachments"])[0]["filename"] == "notes.pdf"

            # The bare file gets a synthesized Documents-resource location so
            # its attachment can still be resolved.
            bare = by_title["syllabus.pdf"]
            assert bare.folder_path == "Unit 1"
            assert bare.location == f"/sections/{SECTION_ID}/documents/99"
            bare_detail = await client.fetch_material_detail(bare)
            assert files_of(bare_detail["attachments"])[0]["filename"] == "syllabus.pdf"
        finally:
            await client.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_name_matching_across_systems():
    assert _names_match("AP Biology", "ap biology")            # case-insensitive
    assert _names_match("AP Biology", "AP Biology - Sec 1")    # section suffix
    assert not _names_match("AP Biology", "AP Physics I")
    # Must never conflate two distinct classes that differ by one token.
    assert not _names_match("AP Calculus AB", "AP Calculus BC")


def test_category_mapping():
    assert _map_category("Chapter 3 Quiz") == "quiz"
    assert _map_category("Cell Respiration Lab") == "lab"
    assert _map_category("Random thing") == "other"


def test_google_url_parsing():
    ref = google_files.parse_google_url("https://docs.google.com/presentation/d/ABC123/edit")
    assert ref and ref.file_id == "ABC123" and ref.kind == "presentation"
    ref2 = google_files.parse_google_url("https://drive.google.com/file/d/XYZ/view")
    assert ref2 and ref2.file_id == "XYZ" and ref2.kind is None
    assert google_files.parse_google_url("https://example.com/notes") is None


# ---------------------------------------------------------------------------
# Provider sync (end-to-end against fakes)
# ---------------------------------------------------------------------------
def test_sync_reconciles_course_and_imports_without_grades(fake_db, monkeypatch):
    provider = SchoologyProvider()

    async def _fake_load(self, user_id):
        return {"secret_ref": "x", "config": {}}

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)

    async def _fake_client(self, integration):
        return _mock_client()

    monkeypatch.setattr(SchoologyProvider, "_client", _fake_client)

    report = asyncio.run(provider.sync(USER_ID))

    assert report["errors"] == []
    # Reconciliation: reused the existing PowerSchool "AP Biology" course, no dupe.
    courses = fake_db.tables["courses"]
    assert len(courses) == 1
    assert courses[0]["metadata"]["schoology_section_id"] == SECTION_ID
    assert report["courses"] == 1
    # The section is active this sync, so the course is a "current" class.
    assert courses[0]["is_active"] is True

    # Assignment imported and linked to the existing course.
    assignments = fake_db.tables["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["course_id"] == BIO_COURSE
    assert assignments[0]["status"] == "not_started"

    # Grading is PowerSchool-only: the provider must never write a grade.
    assert fake_db.tables["grades"] == []

    # Week-at-a-glance: the exam event + the assignment due date are this week;
    # the assignment-linked event and the 2020 event are excluded.
    events = fake_db.tables["calendar_events"]
    kinds = sorted(e["kind"] for e in events)
    assert kinds == ["due", "exam"]

    # Materials + attachments became documents (nested doc's file, the bare
    # file dropped straight into the folder, the assignment's file, and the
    # Google link recorded for later download).
    docs = fake_db.tables["documents"]
    titles = {d["title"] for d in docs}
    assert any("Lab handout" in t or "lab.pdf" in t for t in titles)
    assert any("syllabus.pdf" in t for t in titles)
    # The Google Slides link with no token is stored & flagged for auth.
    google_docs = [d for d in docs if d.get("metadata", {}).get("needs_google_auth")]
    assert google_docs and "docs.google.com" in google_docs[0]["metadata"]["source_url"]


def test_sync_is_idempotent(fake_db, monkeypatch):
    provider = SchoologyProvider()

    async def _fake_load(self, user_id):
        return {"secret_ref": "x", "config": {}}

    async def _fake_client(self, integration):
        return _mock_client()

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)
    monkeypatch.setattr(SchoologyProvider, "_client", _fake_client)

    asyncio.run(provider.sync(USER_ID))
    n_assign = len(fake_db.tables["assignments"])
    n_docs = len(fake_db.tables["documents"])
    asyncio.run(provider.sync(USER_ID))
    # A second sync must not duplicate assignments or re-ingest the same files.
    assert len(fake_db.tables["assignments"]) == n_assign
    assert len(fake_db.tables["documents"]) == n_docs


def test_sync_flips_course_inactive_when_section_grading_period_ends(fake_db, monkeypatch):
    """When Schoology reports a section as no longer active (the grading
    period ended — a completed class), a re-sync must flip is_active to
    False so the UI can show it as completed instead of current."""
    provider = SchoologyProvider()

    async def _fake_load(self, user_id):
        return {"secret_ref": "x", "config": {}}

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)

    async def _active_client(self, integration):
        return _mock_client()

    monkeypatch.setattr(SchoologyProvider, "_client", _active_client)
    asyncio.run(provider.sync(USER_ID))
    assert fake_db.tables["courses"][0]["is_active"] is True

    ended_sections = {"section": [{**SECTIONS["section"][0], "active": 0}]}

    def _ended_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/app-user-info":
            return httpx.Response(200, json={"api_uid": 12345678})
        if path.endswith("/sections") and "/users/" in path:
            return httpx.Response(200, json=ended_sections)
        return _handler(request)

    async def _ended_client(self, integration):
        return SchoologyClient("ckey", "csecret", transport=httpx.MockTransport(_ended_handler))

    monkeypatch.setattr(SchoologyProvider, "_client", _ended_client)
    asyncio.run(provider.sync(USER_ID))
    assert fake_db.tables["courses"][0]["is_active"] is False


def test_normalize_name_basic():
    assert _normalize_name("AP  Calculus-AB!") == "ap calculus ab"


# ---------------------------------------------------------------------------
# Exclusions, clubs, and grouped (lab+AP / AB+BC) courses
# ---------------------------------------------------------------------------
GROUPED_SECTIONS = {"section": [
    {"id": "601", "course_title": "Lunch A", "section_title": "", "course_code": "",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "", "active": 1},
    {"id": "602", "course_title": "AMBUSH 23", "section_title": "", "course_code": "",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "", "active": 1},
    {"id": "603", "course_title": "DECA", "section_title": "Sec 1", "course_code": "",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "", "active": 1},
    {"id": "604", "course_title": "Physics 1 H Ext Lab", "section_title": "", "course_code": "PHYS1H",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "B12", "active": 1},
    {"id": "605", "course_title": "AP Physics 1", "section_title": "", "course_code": "APPHYS1",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "B12", "active": 1},
    {"id": "606", "course_title": "AP Calculus AB", "section_title": "", "course_code": "APCALCAB",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "C4", "active": 1},
    {"id": "607", "course_title": "AP Calculus BC", "section_title": "", "course_code": "APCALCBC",
     "section_code": "", "grading_periods": [1], "meeting_days": [], "start_time": "",
     "end_time": "", "location": "C4", "active": 1},
]}


def _grouped_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/app-user-info":
        return httpx.Response(200, json={"api_uid": 12345678})
    if path.endswith("/sections") and "/users/" in path:
        return httpx.Response(200, json=GROUPED_SECTIONS)
    if "/assignments" in path:
        return httpx.Response(200, json={"assignment": []})
    if "/events" in path:
        return httpx.Response(200, json={"event": []})
    if "/folder/" in path:
        return httpx.Response(200, json={"folder-item": []})
    return httpx.Response(404, json={"error": f"no fixture for {path}"})


def test_sync_excludes_lunch_and_ambush_and_routes_clubs_separately(fake_db, monkeypatch):
    provider = SchoologyProvider()

    async def _fake_load(self, user_id):
        return {"secret_ref": "x", "config": {}}

    async def _fake_client(self, integration):
        return SchoologyClient("ckey", "csecret", transport=httpx.MockTransport(_grouped_handler))

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)
    monkeypatch.setattr(SchoologyProvider, "_client", _fake_client)

    report = asyncio.run(provider.sync(USER_ID))

    assert report["excluded"] == 2  # Lunch A + AMBUSH 23
    assert report["clubs"] == 1  # DECA

    courses = fake_db.tables["courses"]
    names = {c["name"] for c in courses if c["id"] != BIO_COURSE}
    assert "Lunch A" not in names
    assert "AMBUSH 23" not in names
    assert "DECA" not in names

    clubs = fake_db.tables.get("clubs", [])
    assert len(clubs) == 1 and clubs[0]["name"] == "DECA"


def test_sync_merges_lab_and_ap_sections_into_one_grouped_course(fake_db, monkeypatch):
    provider = SchoologyProvider()

    async def _fake_load(self, user_id):
        return {"secret_ref": "x", "config": {}}

    async def _fake_client(self, integration):
        return SchoologyClient("ckey", "csecret", transport=httpx.MockTransport(_grouped_handler))

    monkeypatch.setattr(SchoologyProvider, "_load_integration", _fake_load)
    monkeypatch.setattr(SchoologyProvider, "_client", _fake_client)

    asyncio.run(provider.sync(USER_ID))

    courses = fake_db.tables["courses"]
    physics = [c for c in courses if c["name"] == "AP Physics"]
    assert len(physics) == 2
    by_sem = {c["semester"]: c for c in physics}
    assert by_sem["s1"]["has_hn_prep_lab"] is True
    assert by_sem["s1"]["course_level"] == "honors"
    assert by_sem["s2"]["course_level"] == "ap"
    assert by_sem["s2"]["has_hn_prep_lab"] is False
    # The AP-half links back to the lab-half (the group's root row).
    assert by_sem["s2"]["linked_course_id"] == by_sem["s1"]["id"]

    calc = [c for c in courses if c["name"] == "AP Calculus"]
    assert len(calc) == 2
    calc_by_sem = {c["semester"]: c for c in calc}
    assert calc_by_sem["s1"]["course_level"] == "ap" and not calc_by_sem["s1"]["has_hn_prep_lab"]
    assert calc_by_sem["s2"]["course_level"] == "ap" and not calc_by_sem["s2"]["has_hn_prep_lab"]
    assert calc_by_sem["s2"]["linked_course_id"] == calc_by_sem["s1"]["id"]
