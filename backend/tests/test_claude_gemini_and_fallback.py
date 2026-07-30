"""`app.llm.claude`'s Gemini provider and the automatic same-turn fallback
(a 429 from the primary provider retries once against
`settings.atlas_llm_fallback_provider`). Uses the same fake-httpx approach as
`test_claude_agentic_complete.py` so no real network traffic is involved.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import app.llm.claude as claude_module
from app.config import settings


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient -- returns the next queued response
    for each `.post()` call, in order. A queue entry is either a plain dict
    (wrapped as a 200 response) or a `_FakeResponse` for a specific status."""

    queue: list = []
    calls: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, headers=None, params=None, json=None):
        _FakeAsyncClient.calls.append({"url": url, "json": json, "params": params})
        item = _FakeAsyncClient.queue.pop(0)
        return item if isinstance(item, _FakeResponse) else _FakeResponse(item)


def _groq_message(*, content=None, tool_calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}


def _gemini_text_response(text: str):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def _install(monkeypatch, responses, **settings_overrides):
    _FakeAsyncClient.queue = list(responses)
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(claude_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "atlas_llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "fake-groq-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "atlas_llm_fallback_provider", "gemini")
    for key, value in settings_overrides.items():
        monkeypatch.setattr(settings, key, value)


# ---- Gemini provider itself -------------------------------------------------

def test_complete_gemini_sends_system_instruction_and_role_mapped_history(monkeypatch):
    _install(
        monkeypatch, [_gemini_text_response("Hi there.")],
        atlas_llm_provider="gemini", gemini_api_key="fake-gemini-key",
    )

    result = asyncio.run(claude_module.complete(
        system="You are Atlas.",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
        ],
    ))

    assert result == "Hi there."
    request = _FakeAsyncClient.calls[0]["json"]
    assert request["system_instruction"] == {"parts": [{"text": "You are Atlas."}]}
    assert [c["role"] for c in request["contents"]] == ["user", "model", "user"]


def test_agentic_complete_gemini_executes_a_function_call_and_feeds_result_back(monkeypatch):
    _install(
        monkeypatch,
        [
            {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "add_assignment", "args": {"title": "Lab Report"}}},
            ]}}]},
            _gemini_text_response('Added "Lab Report" for you.'),
        ],
        atlas_llm_provider="gemini", gemini_api_key="fake-gemini-key",
    )

    calls = []

    async def _execute(name, args):
        calls.append((name, args))
        return {"status": "done", "assignment": {"id": "a1", "title": args["title"]}}

    result = asyncio.run(claude_module.agentic_complete(
        system="sys", messages=[{"role": "user", "content": "add lab report as an assignment"}],
        tools=[{"name": "add_assignment", "description": "...",
                "parameters": {"type": "object", "properties": {}}}],
        execute_tool=_execute,
    ))

    assert calls == [("add_assignment", {"title": "Lab Report"})]
    assert result["text"] == 'Added "Lab Report" for you.'
    assert result["tool_calls"][0]["result"]["status"] == "done"

    second_request = _FakeAsyncClient.calls[1]["json"]
    function_response_parts = [
        p for m in second_request["contents"] for p in m["parts"] if "functionResponse" in p
    ]
    assert len(function_response_parts) == 1
    assert function_response_parts[0]["functionResponse"]["response"]["status"] == "done"


# ---- Automatic fallback on 429 ----------------------------------------------

def test_complete_falls_back_to_gemini_after_groq_429(monkeypatch):
    _install(
        monkeypatch,
        [
            _FakeResponse(status_code=429, payload={"error": "rate limited"}),
            _gemini_text_response("Handled via Gemini."),
        ],
        gemini_api_key="fake-gemini-key",
    )

    result = asyncio.run(claude_module.complete(
        system="sys", messages=[{"role": "user", "content": "hi"}],
    ))

    assert result == "Handled via Gemini."
    assert len(_FakeAsyncClient.calls) == 2
    assert "groq.com" in _FakeAsyncClient.calls[0]["url"]
    assert "generativelanguage.googleapis.com" in _FakeAsyncClient.calls[1]["url"]


def test_agentic_complete_falls_back_to_gemini_after_groq_429(monkeypatch):
    _install(
        monkeypatch,
        [
            _FakeResponse(status_code=429, payload={"error": "rate limited"}),
            _gemini_text_response("Handled via Gemini."),
        ],
        gemini_api_key="fake-gemini-key",
    )

    async def _execute(name, args):
        raise AssertionError("no tool should have been called")

    result = asyncio.run(claude_module.agentic_complete(
        system="sys", messages=[{"role": "user", "content": "hi"}],
        tools=[], execute_tool=_execute,
    ))

    assert result == {"text": "Handled via Gemini.", "tool_calls": []}
    assert len(_FakeAsyncClient.calls) == 2


def test_complete_does_not_fall_back_when_fallback_has_no_credentials(monkeypatch):
    # gemini_api_key left empty by _install's default -- fallback is
    # configured by name but not actually usable, so the original 429 must
    # surface rather than trying (and guaranteed-failing) a second call.
    _install(monkeypatch, [_FakeResponse(status_code=429, payload={"error": "rate limited"})])

    with pytest.raises(claude_module.LLMError) as exc_info:
        asyncio.run(claude_module.complete(
            system="sys", messages=[{"role": "user", "content": "hi"}],
        ))

    assert exc_info.value.status == 429
    assert len(_FakeAsyncClient.calls) == 1


def test_complete_does_not_fall_back_on_a_non_429_error(monkeypatch):
    # A 500 from the primary provider isn't a rate limit -- retrying against
    # the fallback wouldn't help diagnose it, so it should surface directly.
    _install(
        monkeypatch,
        [_FakeResponse(status_code=500, payload={"error": "server error"})],
        gemini_api_key="fake-gemini-key",
    )

    with pytest.raises(claude_module.LLMError) as exc_info:
        asyncio.run(claude_module.complete(
            system="sys", messages=[{"role": "user", "content": "hi"}],
        ))

    assert exc_info.value.status == 500
    assert len(_FakeAsyncClient.calls) == 1


def test_complete_does_not_fall_back_when_fallback_provider_is_disabled(monkeypatch):
    _install(
        monkeypatch,
        [_FakeResponse(status_code=429, payload={"error": "rate limited"})],
        gemini_api_key="fake-gemini-key", atlas_llm_fallback_provider="",
    )

    with pytest.raises(claude_module.LLMError):
        asyncio.run(claude_module.complete(
            system="sys", messages=[{"role": "user", "content": "hi"}],
        ))

    assert len(_FakeAsyncClient.calls) == 1
