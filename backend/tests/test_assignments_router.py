"""`app.routers.assignments` -- the duplicate-review endpoints, plus a
regression check for route registration order.

`/assignments/possible-duplicates` is a static path living under the same
`/assignments` prefix as the generic CRUD router's `GET /assignments/{row_id}`
(see app.core.crud). FastAPI matches routes in registration order, so if the
CRUD router's catch-all is ever registered first, "possible-duplicates"
would get swallowed as a literal row id and 404 -- see the comment above
`assignments.router`'s inclusion in app.routers.__init__.
"""
from __future__ import annotations

import uuid

from starlette.testclient import TestClient

import app.routers.assignments as assignments_router
from app.core.security import CurrentUser, get_current_user
from app.main import app

USER_ID = str(uuid.uuid4())
client = TestClient(app)


def _as_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")


def test_possible_duplicates_route_is_not_shadowed_by_the_crud_row_id_route(monkeypatch):
    async def _fake_find(user_id):
        assert user_id == USER_ID
        return [{"score": 0.9}]

    monkeypatch.setattr(assignments_router.assignment_dedupe, "find_possible_duplicates", _fake_find)
    _as_user()
    try:
        r = client.get("/api/v1/assignments/possible-duplicates")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200
    assert r.json() == [{"score": 0.9}]


def test_merge_endpoint_delegates_to_the_service(monkeypatch):
    keep_id, discard_id = str(uuid.uuid4()), str(uuid.uuid4())
    captured = {}

    async def _fake_merge(user_id, keep, discard):
        captured.update(user_id=user_id, keep=keep, discard=discard)
        return {"id": keep}

    monkeypatch.setattr(assignments_router.assignment_dedupe, "merge_assignments", _fake_merge)
    _as_user()
    try:
        r = client.post("/api/v1/assignments/merge", json={"keep_id": keep_id, "discard_id": discard_id})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200
    assert r.json() == {"id": keep_id}
    assert captured == {"user_id": USER_ID, "keep": keep_id, "discard": discard_id}


def test_merge_endpoint_returns_404_when_not_found(monkeypatch):
    async def _fake_merge(user_id, keep, discard):
        raise LookupError("nope")

    monkeypatch.setattr(assignments_router.assignment_dedupe, "merge_assignments", _fake_merge)
    _as_user()
    try:
        r = client.post(
            "/api/v1/assignments/merge",
            json={"keep_id": str(uuid.uuid4()), "discard_id": str(uuid.uuid4())},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 404


def test_dismiss_duplicate_endpoint_delegates_to_the_service(monkeypatch):
    captured = {}

    async def _fake_dismiss(user_id, a, b):
        captured.update(user_id=user_id, a=a, b=b)

    monkeypatch.setattr(assignments_router.assignment_dedupe, "dismiss_duplicate", _fake_dismiss)
    _as_user()
    try:
        r = client.post(
            "/api/v1/assignments/dismiss-duplicate",
            json={"assignment_id_a": "a-id", "assignment_id_b": "b-id"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 204
    assert captured == {"user_id": USER_ID, "a": "a-id", "b": "b-id"}
