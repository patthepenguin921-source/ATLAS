"""Smoke + unit tests that run without external services."""
import asyncio
import math

import httpx
from starlette.testclient import TestClient

from app.core.supabase_client import SupabaseClient, _strip_null_bytes
from app.embeddings.embedder import DIM, embed_text
from app.llm.claude import _extract_json
from app.main import app
from app.routers.documents import _safe_storage_name
from app.services.ingestion import _sanitize_text, chunk_text
from app.services.knowledge_model import _retention

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_auth_required():
    assert client.get("/api/v1/courses").status_code == 401
    assert client.get("/api/v1/dashboard").status_code == 401


def test_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/v1/agents/chat", "/api/v1/documents/upload",
              "/api/v1/search/ask", "/api/v1/knowledge/graph"):
        assert p in paths


def test_embedding_normalized():
    v = asyncio.run(embed_text("photosynthesis converts light to chemical energy"))
    assert len(v) == DIM
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6


def test_chunking():
    chunks = chunk_text("sentence. " * 400)
    assert len(chunks) > 1
    assert all(chunks)


def test_retention_decay():
    assert _retention(1, 0.0) > _retention(1, 10.0)
    assert _retention(30, 5.0) > _retention(1, 5.0)  # more spacing => slower decay


def test_extract_json_from_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('noise {"b": 2} trailing') == {"b": 2}


def test_sanitize_text_strips_null_bytes_and_surrogates():
    assert _sanitize_text("hello\x00world") == "helloworld"
    assert _sanitize_text("clean") == "clean"
    assert _sanitize_text("bad\ud800pdf\udffftext") == "badpdftext"


def test_strip_null_bytes_payload():
    payload = {
        "title": "a\x00b\ud800",
        "chunks": ["x\x00", {"content": "y\x00z"}],
        "n": None,
    }
    assert _strip_null_bytes(payload) == {
        "title": "ab", "chunks": ["x", {"content": "yz"}], "n": None,
    }


def test_insert_upsert_sends_on_conflict(monkeypatch):
    """Regression test: PostgREST's merge-duplicates resolves against the
    table's primary key unless told otherwise via ?on_conflict=. Several
    call sites upsert on a *different* unique constraint (e.g. integrations'
    (user_id, provider)) without ever supplying the real primary key, so
    without on_conflict every "upsert" was actually a plain insert that
    409s on retry instead of updating."""
    from app.config import settings

    monkeypatch.setattr(settings, "supabase_url", "https://fake.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "fake-key")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["prefer"] = request.headers.get("prefer")
        return httpx.Response(201, json=[{"id": "abc"}])

    sb = SupabaseClient()
    sb._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    asyncio.run(sb.insert(
        "integrations", {"user_id": "u1", "provider": "powerschool"},
        upsert=True, on_conflict="user_id,provider",
    ))

    assert "on_conflict=user_id%2Cprovider" in captured["url"] or "on_conflict=user_id,provider" in captured["url"]
    assert "resolution=merge-duplicates" in captured["prefer"]


def test_safe_storage_name_strips_unsupported_unicode():
    # Supabase Storage keys only accept an S3-safe, mostly-ASCII charset —
    # em dashes, curly quotes, etc. used to make the upload silently fail.
    assert _safe_storage_name("Overview – Vercel.pdf") == "Overview _ Vercel.pdf"
    assert _safe_storage_name("SE States (6).pdf") == "SE States (6).pdf"
    assert _safe_storage_name("a/b.pdf") == "a_b.pdf"
    assert _safe_storage_name("") == "document"
