"""`GET /search/text` -- fast structured text search across assignments +
documents, extended to filter by course (for the course page's own document
search) and by each item's existing tag (`doc_type` for documents, `category`
for assignments -- the "search by tags" the dashboard's quick search now
supports) and to work as a plain browse (no `q`) when a tag/course filter
alone is enough to narrow the list.
"""
from __future__ import annotations

import asyncio
import uuid

import app.routers.search as search_module
from app.core.security import CurrentUser

USER_ID = str(uuid.uuid4())
COURSE_ID = str(uuid.uuid4())
USER = CurrentUser(id=USER_ID, email="student@example.com")


def _install_fake_select(monkeypatch, *, assignments=None, documents_by_call=None):
    """`documents_by_call` lets title-match and content-match calls (both hit
    the `documents` table) return different rows, keyed by call order."""
    calls: list[tuple[str, dict]] = []
    doc_call_index = {"n": 0}

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        calls.append((table, dict(filters or {})))
        if table == "assignments":
            return assignments or []
        if table == "documents":
            rows = (documents_by_call or [[]])[min(doc_call_index["n"], len(documents_by_call or [[]]) - 1)]
            doc_call_index["n"] += 1
            return rows
        return []

    monkeypatch.setattr(search_module.supabase, "select", _fake_select)
    return calls


def test_text_search_with_query_matches_title_and_falls_back_to_content(monkeypatch):
    calls = _install_fake_select(
        monkeypatch,
        assignments=[{"id": "a1", "title": "Photosynthesis Lab", "category": "lab",
                      "course_id": COURSE_ID, "due_date": "2026-09-01"}],
        documents_by_call=[
            [{"id": "d1", "title": "Photosynthesis Notes", "doc_type": "notes", "course_id": COURSE_ID}],
            [{"id": "d1", "title": "Photosynthesis Notes", "doc_type": "notes", "course_id": COURSE_ID,
              "extracted_text": "...photosynthesis happens in the chloroplast..."},
             {"id": "d2", "title": "Unrelated", "doc_type": "other", "course_id": COURSE_ID,
              "extracted_text": "mentions photosynthesis deep in the body text"}],
        ],
    )

    result = asyncio.run(search_module.text_search(q="photosynthesis", user=USER))

    assert result["assignments"][0]["id"] == "a1"
    doc_ids = [d["id"] for d in result["documents"]]
    assert doc_ids == ["d1", "d2"]  # d1 not duplicated; d2 found via content only
    assert "snippet" in result["documents"][1]

    assignment_call = next(f for t, f in calls if t == "assignments")
    assert assignment_call["title"] == "ilike.*photosynthesis*"


def test_text_search_scopes_by_course_id(monkeypatch):
    calls = _install_fake_select(monkeypatch, assignments=[], documents_by_call=[[], []])

    asyncio.run(search_module.text_search(q="quiz", course_id=COURSE_ID, user=USER))

    assignment_call = next(f for t, f in calls if t == "assignments")
    doc_calls = [f for t, f in calls if t == "documents"]
    assert assignment_call["course_id"] == f"eq.{COURSE_ID}"
    assert all(f["course_id"] == f"eq.{COURSE_ID}" for f in doc_calls)


def test_text_search_filters_documents_by_doc_type_with_no_query(monkeypatch):
    calls = _install_fake_select(
        monkeypatch,
        documents_by_call=[[{"id": "d1", "title": "Unit 1 at a Glance", "doc_type": "glance",
                             "course_id": COURSE_ID}]],
    )

    result = asyncio.run(search_module.text_search(q="", doc_type="glance", user=USER))

    assert result["assignments"] == []
    assert [d["id"] for d in result["documents"]] == ["d1"]
    doc_call = next(f for t, f in calls if t == "documents")
    assert doc_call["doc_type"] == "eq.glance"
    assert "title" not in doc_call  # browse mode, no text match filter


def test_text_search_filters_assignments_by_category_with_no_query(monkeypatch):
    calls = _install_fake_select(
        monkeypatch,
        assignments=[{"id": "a1", "title": "Ch. 4 Quiz", "category": "quiz",
                      "course_id": COURSE_ID, "due_date": "2026-09-05"}],
        documents_by_call=[[]],
    )

    result = asyncio.run(search_module.text_search(q="", category="quiz", user=USER))

    assert [a["id"] for a in result["assignments"]] == ["a1"]
    assignment_call = next(f for t, f in calls if t == "assignments")
    assert assignment_call["category"] == "eq.quiz"
    assert "title" not in assignment_call


def test_text_search_with_no_query_and_no_filters_returns_no_assignments(monkeypatch):
    """An empty query with nothing to narrow by shouldn't dump every
    assignment ever created -- only documents support a plain "browse
    everything" listing (bounded by `limit`), since that's the course-page
    document-search use case; assignments still need a real query or tag."""
    _install_fake_select(monkeypatch, assignments=[{"id": "a1", "title": "x", "category": "other",
                                                     "course_id": COURSE_ID, "due_date": None}],
                          documents_by_call=[[]])

    result = asyncio.run(search_module.text_search(q="", user=USER))

    assert result["assignments"] == []
