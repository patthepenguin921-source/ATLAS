"""`Agent.respond` surfaces at most one `pending_action` per turn when the
model proposes a destructive tool call (delete_*) -- the confirm-before-
delete contract lives in `app.agents.tools`, but this is what carries that
signal out of the chat turn for the API/frontend to act on.
"""
from __future__ import annotations

import asyncio

from app.agents.base import Agent
from app.llm import claude
from app.services import memory, web_search

USER_ID = "user-1"


def _install_context(monkeypatch):
    async def _fake_build_context(
        user_id, message, *, include_semantic=True, course_id=None, folder_id=None,
    ):
        return {"courses": [], "upcoming": [], "relevant_passages": []}

    async def _fake_search(query):
        return [], None

    monkeypatch.setattr(memory, "build_context", _fake_build_context)
    monkeypatch.setattr(memory, "render_context", lambda ctx: "")
    monkeypatch.setattr(web_search, "search", _fake_search)


def test_respond_has_no_pending_action_for_a_plain_reply(monkeypatch):
    _install_context(monkeypatch)

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        return {"text": "Sure, here's the answer.", "tool_calls": []}

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    result = asyncio.run(Agent().respond(USER_ID, "what's my grade in bio?"))

    assert result["reply"] == "Sure, here's the answer."
    assert result["pending_action"] is None


def test_respond_surfaces_a_pending_action_from_a_proposed_delete(monkeypatch):
    _install_context(monkeypatch)

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        return {
            "text": 'Delete the "Old Homework" assignment for AP Biology?',
            "tool_calls": [{
                "name": "delete_assignment", "arguments": {"title": "Old Homework"},
                "result": {
                    "status": "pending_confirmation",
                    "description": 'Delete "Old Homework"?',
                    "action": {"name": "delete_assignment", "arguments": {"id": "a1"}},
                },
            }],
        }

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    result = asyncio.run(Agent().respond(USER_ID, "delete my old homework assignment"))

    assert result["pending_action"] == {
        "name": "delete_assignment", "arguments": {"id": "a1"}, "description": 'Delete "Old Homework"?',
    }


def test_respond_ignores_a_completed_non_destructive_tool_call(monkeypatch):
    _install_context(monkeypatch)

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        return {
            "text": 'Added "Lab Report" for you.',
            "tool_calls": [{
                "name": "add_assignment", "arguments": {"title": "Lab Report"},
                "result": {"status": "done", "assignment": {"id": "a1"}},
            }],
        }

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    result = asyncio.run(Agent().respond(USER_ID, "add lab report as an assignment"))

    assert result["pending_action"] is None
