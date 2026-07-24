"""PATCH /documents/{id} — covers the importance-rating override path
specifically: setting `importance` must stamp `importance_source =
"manual"` so a later Archivist re-enrichment never quietly reverts it (see
test_archivist_importance.py for that side).
"""
from __future__ import annotations

import uuid

from starlette.testclient import TestClient

import app.routers.documents as documents_router
from app.core.security import CurrentUser, get_current_user
from app.main import app

USER_ID = str(uuid.uuid4())
client = TestClient(app)


def test_patch_importance_stamps_manual_source(monkeypatch):
    doc_id = str(uuid.uuid4())
    captured: dict = {}

    async def _fake_update(table, patch, *, filters):
        captured["table"] = table
        captured["patch"] = patch
        return [{"id": doc_id, **patch}]

    monkeypatch.setattr(documents_router.supabase, "update", _fake_update)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    try:
        r = client.patch(f"/api/v1/documents/{doc_id}", json={"importance": "high"})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert captured["patch"]["importance"] == "high"
    assert captured["patch"]["importance_source"] == "manual"


def test_patch_without_importance_does_not_touch_importance_source(monkeypatch):
    doc_id = str(uuid.uuid4())
    captured: dict = {}

    async def _fake_update(table, patch, *, filters):
        captured["patch"] = patch
        return [{"id": doc_id, **patch}]

    monkeypatch.setattr(documents_router.supabase, "update", _fake_update)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    try:
        r = client.patch(f"/api/v1/documents/{doc_id}", json={"title": "New title"})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert "importance_source" not in captured["patch"]
