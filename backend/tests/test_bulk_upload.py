"""End-to-end exercise of the bulk-upload + course auto-detection flow.

Runs the real FastAPI route and the real `_store_and_ingest` pipeline against
an in-memory fake of the Supabase REST/storage layer, so it proves the actual
wiring (multi-file parsing, classification threshold, needs_review flagging,
the PATCH review-correction endpoint) without needing live Supabase/LLM
credentials.
"""
from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.agents.archivist import Archivist
from app.config import settings
from app.core.r2_client import r2
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import supabase
from app.main import app

USER_ID = str(uuid.uuid4())
BIO_COURSE = str(uuid.uuid4())
MATH_COURSE = str(uuid.uuid4())


class FakeSupabase:
    """Minimal in-memory stand-in for the pieces `_store_and_ingest` touches."""

    def __init__(self):
        self.tables: dict[str, list[dict[str, Any]]] = {
            "courses": [
                {"id": BIO_COURSE, "user_id": USER_ID, "name": "AP Biology", "subject": "science"},
                {"id": MATH_COURSE, "user_id": USER_ID, "name": "Algebra II", "subject": "math"},
            ],
            "documents": [],
            "document_chunks": [],
        }

    @staticmethod
    def _matches(row: dict, filters: dict[str, str] | None) -> bool:
        for k, v in (filters or {}).items():
            want = v.split("eq.", 1)[1] if isinstance(v, str) and v.startswith("eq.") else v
            if str(row.get(k)) != str(want):
                return False
        return True

    async def select(self, table, *, columns="*", filters=None, order=None, limit=None, single=False):
        rows = [r for r in self.tables.setdefault(table, []) if self._matches(r, filters)]
        return rows

    async def insert(self, table, rows, *, upsert=False):
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
            if self._matches(row, filters):
                row.update(patch)
                out.append(row)
        return out

    async def delete(self, table, *, filters):
        keep, removed = [], []
        for row in self.tables.setdefault(table, []):
            (removed if self._matches(row, filters) else keep).append(row)
        self.tables[table] = keep
        return removed


class FakeR2:
    """Minimal in-memory stand-in for the R2 object store."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, key, content, content_type):
        self.objects[key] = content

    def signed_url(self, key, expires_in=3600):
        return f"https://fake-r2/{key}"

    async def remove(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabase()
    for name in ("select", "insert", "update", "delete"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    fake_storage = FakeR2()
    for name in ("upload", "signed_url", "remove"):
        monkeypatch.setattr(r2, name, getattr(fake_storage, name))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    yield fake
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client(fake_db):
    return TestClient(app)


def test_bulk_upload_confident_and_low_confidence(client, fake_db, monkeypatch):
    async def fake_classify(self, text, courses):
        if "photosynthesis" in text:
            return {"course_id": BIO_COURSE, "confidence": 0.95}
        return {"course_id": MATH_COURSE, "confidence": 0.2}

    monkeypatch.setattr(Archivist, "classify_course", fake_classify)
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")  # settings.has_llm -> True

    files = [
        ("files", ("bio.txt", io.BytesIO(b"Notes on photosynthesis and cell respiration."), "text/plain")),
        ("files", ("mystery.txt", io.BytesIO(b"assorted notes with no obvious subject"), "text/plain")),
    ]
    res = client.post("/api/v1/documents/bulk-upload", files=files)
    assert res.status_code == 201
    results = {r["filename"]: r for r in res.json()["results"]}

    bio = results["bio.txt"]
    assert bio["course_id"] == BIO_COURSE
    assert bio["needs_review"] is False

    mystery = results["mystery.txt"]
    assert mystery["course_id"] == MATH_COURSE
    assert mystery["needs_review"] is True
    assert mystery["course_confidence"] == 0.2

    # The low-confidence doc is filed, not dropped — original + record both exist.
    doc = next(d for d in fake_db.tables["documents"] if d["id"] == mystery["id"])
    assert doc["course_id"] == MATH_COURSE

    # Review screen correction: PATCH clears needs_review.
    patch_res = client.patch(f"/api/v1/documents/{mystery['id']}", json={"course_id": BIO_COURSE})
    assert patch_res.status_code == 200
    assert patch_res.json()["course_id"] == BIO_COURSE
    assert patch_res.json()["needs_review"] is False


def test_bulk_upload_no_llm_still_files_document(client, fake_db, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")  # settings.has_llm -> False
    files = [("files", ("notes.txt", io.BytesIO(b"some notes"), "text/plain"))]
    res = client.post("/api/v1/documents/bulk-upload", files=files)
    assert res.status_code == 201
    result = res.json()["results"][0]
    assert result["needs_review"] is True
    assert result["course_id"] in (BIO_COURSE, MATH_COURSE)


def test_bulk_upload_no_courses_leaves_unfiled_but_flagged(client, fake_db, monkeypatch):
    fake_db.tables["courses"] = []
    files = [("files", ("notes.txt", io.BytesIO(b"some notes"), "text/plain"))]
    res = client.post("/api/v1/documents/bulk-upload", files=files)
    assert res.status_code == 201
    result = res.json()["results"][0]
    assert result["needs_review"] is True
    assert result["course_id"] is None
