"""Deleting a document removes its row immediately, but the underlying R2
file is queued for removal instead of being deleted inline — a 24-hour
grace window (`app.services.storage_cleanup`) before it's actually gone,
swept by a scheduled cron route.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from starlette.testclient import TestClient

import app.routers.documents as documents_router
from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import storage_cleanup

USER_ID = str(uuid.uuid4())
client = TestClient(app)


def _auth():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    return get_current_user


def test_delete_document_queues_r2_removal_instead_of_deleting_inline(monkeypatch):
    doc_id = str(uuid.uuid4())
    storage_path = f"{USER_ID}/{doc_id}/file.pdf"
    deleted_doc_rows: list[dict] = []
    queued: list[str] = []
    r2_remove_calls: list[str] = []

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "documents"
        return [{"storage_path": storage_path}]

    async def _fake_delete(table, *, filters):
        deleted_doc_rows.append({"table": table, "filters": filters})
        return [{"id": doc_id}]

    async def _fake_queue_deletion(path):
        queued.append(path)

    async def _fake_r2_remove(key):
        r2_remove_calls.append(key)

    monkeypatch.setattr(documents_router.supabase, "select", _fake_select)
    monkeypatch.setattr(documents_router.supabase, "delete", _fake_delete)
    monkeypatch.setattr(storage_cleanup, "queue_deletion", _fake_queue_deletion)
    monkeypatch.setattr(documents_router.r2, "remove", _fake_r2_remove)

    dep = _auth()
    try:
        r = client.delete(f"/api/v1/documents/{doc_id}")
        assert r.status_code == 204
    finally:
        app.dependency_overrides.pop(dep, None)

    assert deleted_doc_rows and deleted_doc_rows[0]["table"] == "documents"
    assert queued == [storage_path]
    assert r2_remove_calls == []  # not removed from R2 inline


def test_purge_expired_removes_only_objects_past_the_grace_window(monkeypatch):
    now = datetime.now(timezone.utc)
    old_row = {"id": "row-old", "storage_path": "u1/old/file.pdf",
               "requested_at": (now - timedelta(hours=25)).isoformat()}
    recent_row = {"id": "row-recent", "storage_path": "u1/recent/file.pdf",
                  "requested_at": (now - timedelta(hours=1)).isoformat()}

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        assert table == "pending_r2_deletions"
        assert filters["requested_at"].startswith("lt.")
        # A real Postgres filter would exclude recent_row server-side —
        # simulate that here rather than re-implementing date comparison.
        return [old_row]

    removed_paths: list[str] = []
    deleted_tracking_rows: list[dict] = []

    async def _fake_r2_remove(key):
        removed_paths.append(key)

    async def _fake_delete(table, *, filters):
        deleted_tracking_rows.append({"table": table, "filters": filters})
        return [{"id": old_row["id"]}]

    monkeypatch.setattr(storage_cleanup.supabase, "select", _fake_select)
    monkeypatch.setattr(storage_cleanup.r2, "remove", _fake_r2_remove)
    monkeypatch.setattr(storage_cleanup.supabase, "delete", _fake_delete)

    result = asyncio.run(storage_cleanup.purge_expired())

    assert removed_paths == [old_row["storage_path"]]
    assert deleted_tracking_rows[0]["table"] == "pending_r2_deletions"
    assert result == {"removed": 1, "failed": 0, "checked": 1}


def test_purge_expired_leaves_the_tracking_row_when_r2_removal_fails(monkeypatch):
    row = {"id": "row-1", "storage_path": "u1/x/file.pdf",
           "requested_at": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()}

    async def _fake_select(table, *, columns="*", filters=None, order=None, limit=None, single=False):
        return [row]

    async def _failing_remove(key):
        raise RuntimeError("R2 down")

    delete_calls: list[Any] = []

    async def _fake_delete(table, *, filters):
        delete_calls.append(filters)
        return []

    monkeypatch.setattr(storage_cleanup.supabase, "select", _fake_select)
    monkeypatch.setattr(storage_cleanup.r2, "remove", _failing_remove)
    monkeypatch.setattr(storage_cleanup.supabase, "delete", _fake_delete)

    result = asyncio.run(storage_cleanup.purge_expired())

    assert result == {"removed": 0, "failed": 1, "checked": 1}
    assert delete_calls == []  # tracking row kept for the next sweep to retry


def test_purge_cron_endpoint_requires_the_cron_secret(monkeypatch):
    monkeypatch.setattr(settings, "atlas_cron_secret", "s3cr3t")
    r = client.get("/api/v1/documents/cron/purge-deleted")
    assert r.status_code == 401

    async def _fake_purge():
        return {"removed": 0, "failed": 0, "checked": 0}

    monkeypatch.setattr(storage_cleanup, "purge_expired", _fake_purge)
    r = client.get("/api/v1/documents/cron/purge-deleted", headers={"X-Cron-Secret": "s3cr3t"})
    assert r.status_code == 200
    assert r.json() == {"removed": 0, "failed": 0, "checked": 0}
