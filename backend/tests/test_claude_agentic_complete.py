"""`app.llm.claude.agentic_complete` -- the provider-agnostic tool-use loop
that lets an agent actually perform an action ("add this as an assignment")
instead of only ever replying with text. Exercised against the Groq path
(the default provider, `settings.atlas_llm_provider == "groq"`) since Groq's
OpenAI-compatible chat-completions API is called directly via httpx here,
easy to fake with a mock transport-free stand-in; the Anthropic path goes
through the official SDK client and isn't separately unit-tested.
"""
from __future__ import annotations

import asyncio
import json

import app.llm.claude as claude_module
from app.config import settings


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient -- returns the next queued response
    for each `.post()` call, in order, so a test can script a multi-round
    tool-use exchange with no real network traffic."""

    queue: list[dict] = []
    calls: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, headers=None, json=None):
        _FakeAsyncClient.calls.append(json)
        return _FakeResponse(_FakeAsyncClient.queue.pop(0))


def _groq_message(*, content=None, tool_calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}


def _install(monkeypatch, responses):
    _FakeAsyncClient.queue = list(responses)
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(claude_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "atlas_llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")


def test_agentic_complete_returns_plain_text_with_no_tool_calls(monkeypatch):
    _install(monkeypatch, [_groq_message(content="Just a normal reply.")])

    async def _execute(name, args):
        raise AssertionError("no tool should have been called")

    result = asyncio.run(claude_module.agentic_complete(
        system="sys", messages=[{"role": "user", "content": "hi"}],
        tools=[], execute_tool=_execute,
    ))

    assert result == {"text": "Just a normal reply.", "tool_calls": []}


def test_agentic_complete_executes_a_tool_call_and_feeds_the_result_back(monkeypatch):
    _install(monkeypatch, [
        _groq_message(tool_calls=[{
            "id": "call_1",
            "function": {"name": "add_assignment", "arguments": json.dumps({"title": "Lab Report"})},
        }]),
        _groq_message(content='Added "Lab Report" for you.'),
    ])

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
    assert result["tool_calls"][0]["name"] == "add_assignment"
    assert result["tool_calls"][0]["result"]["status"] == "done"

    # The second request round actually included the tool result fed back.
    second_request = _FakeAsyncClient.calls[1]
    tool_messages = [m for m in second_request["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0]["content"])["status"] == "done"


def test_agentic_complete_stops_after_max_rounds_without_looping_forever(monkeypatch):
    # Every round proposes another tool call -- the model never converges
    # on plain text. The safety valve must still return, not hang forever.
    always_calls = _groq_message(tool_calls=[{
        "id": "call_x", "function": {"name": "noop", "arguments": "{}"},
    }])
    _install(monkeypatch, [always_calls] * claude_module._MAX_TOOL_ROUNDS)

    async def _execute(name, args):
        return {"status": "done"}

    result = asyncio.run(claude_module.agentic_complete(
        system="sys", messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "noop", "description": "...", "parameters": {"type": "object", "properties": {}}}],
        execute_tool=_execute,
    ))

    assert len(result["tool_calls"]) == claude_module._MAX_TOOL_ROUNDS
    assert len(_FakeAsyncClient.calls) == claude_module._MAX_TOOL_ROUNDS
