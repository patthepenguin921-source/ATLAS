"""Smoke + unit tests that run without external services."""
import asyncio
import math

from starlette.testclient import TestClient

from app.core.supabase_client import _strip_null_bytes
from app.embeddings.embedder import DIM, embed_text
from app.llm.claude import _extract_json
from app.main import app
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
