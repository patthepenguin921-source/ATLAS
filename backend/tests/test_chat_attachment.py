"""Chat-composer document attachments (`ChatAttachButton` on the frontend).

Two paths:
- "Save to Documents" reuses the existing upload + process endpoints
  unchanged, so it needs no new backend test coverage here.
- "Use for this conversation only" goes through the new
  `POST /documents/extract-temp` endpoint (extracts text, persists nothing)
  and `ChatRequest.attachment_text` (injected into that turn's context only,
  via `Agent.respond` -> `app.services.memory.render_context`).
"""
from __future__ import annotations

import asyncio
import io

from starlette.testclient import TestClient

from app.agents.base import Agent
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import supabase
from app.llm import claude
from app.main import app
from app.services import memory, web_search

USER_ID = "user-1"


# ---- POST /documents/extract-temp -------------------------------------

def _client():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="u@test")
    return TestClient(app)


def test_extract_temp_returns_text_without_persisting_anything(monkeypatch):
    inserted = []
    uploaded = []

    async def _fake_insert(table, rows, **kwargs):
        inserted.append((table, rows))
        return []

    async def _fake_upload(path, content, content_type):
        uploaded.append(path)

    monkeypatch.setattr(supabase, "insert", _fake_insert)
    from app.core.r2_client import r2
    monkeypatch.setattr(r2, "upload", _fake_upload)

    client = _client()
    try:
        res = client.post(
            "/api/v1/documents/extract-temp",
            files={"file": ("notes.txt", io.BytesIO(b"Photosynthesis converts light to energy."), "text/plain")},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "notes.txt"
    assert "Photosynthesis" in body["text"]
    assert body["truncated"] is False
    # Nothing was written to Postgres or R2 -- this is the whole point of
    # the "temporary" path versus the real /upload endpoint.
    assert inserted == []
    assert uploaded == []


def test_extract_temp_truncates_to_the_char_cap(monkeypatch):
    import app.routers.documents as documents_module
    monkeypatch.setattr(documents_module, "_TEMP_ATTACHMENT_CHAR_LIMIT", 20)

    client = _client()
    try:
        res = client.post(
            "/api/v1/documents/extract-temp",
            files={"file": ("big.txt", io.BytesIO(b"x" * 500), "text/plain")},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert res.status_code == 200
    body = res.json()
    assert len(body["text"]) == 20
    assert body["truncated"] is True


def test_extract_temp_rejects_an_empty_file():
    client = _client()
    try:
        res = client.post(
            "/api/v1/documents/extract-temp",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert res.status_code == 400


# ---- Agent.respond(attachment_text=...) --------------------------------

def _install_context(monkeypatch):
    async def _fake_build_context(user_id, message, *, include_semantic=True):
        return {"courses": [], "upcoming": []}

    async def _fake_search(query):
        return [], None

    monkeypatch.setattr(memory, "build_context", _fake_build_context)
    monkeypatch.setattr(web_search, "search", _fake_search)


def test_attachment_text_reaches_the_system_prompt(monkeypatch):
    _install_context(monkeypatch)
    seen = {}

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        seen["system"] = system
        return {"text": "Here's what that says.", "tool_calls": []}

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    result = asyncio.run(Agent().respond(
        USER_ID, "what does this say?",
        attachment_text="Photosynthesis converts light to chemical energy.",
        attachment_filename="notes.txt",
    ))

    assert result["reply"] == "Here's what that says."
    assert "notes.txt" in seen["system"]
    assert "Photosynthesis converts light to chemical energy." in seen["system"]
    assert "not saved to the student's Documents" in seen["system"]


def test_no_attachment_section_when_nothing_was_attached(monkeypatch):
    _install_context(monkeypatch)
    seen = {}

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        seen["system"] = system
        return {"text": "ok", "tool_calls": []}

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    asyncio.run(Agent().respond(USER_ID, "hello"))

    assert "Attached to this conversation" not in seen["system"]
