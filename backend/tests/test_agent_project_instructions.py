"""`Agent.respond`'s `project_instructions` param -- custom instructions a
student sets on a chat project (see `chat_projects.instructions`, migration
0027) get folded into the system prompt for every chat filed under that
project, but must never be presented as capable of overriding Atlas's own
operating principles (no fabricating grades, no skipping delete confirms).
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


def test_project_instructions_are_folded_into_the_system_prompt(monkeypatch):
    _install_context(monkeypatch)
    seen_system = {}

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        seen_system["value"] = system
        return {"text": "ok", "tool_calls": []}

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    asyncio.run(Agent().respond(
        USER_ID, "hi", project_instructions="Always answer in French and cite page numbers.",
    ))

    assert "Always answer in French and cite page numbers." in seen_system["value"]
    assert "CUSTOM INSTRUCTIONS FOR THIS PROJECT" in seen_system["value"]


def test_no_project_instructions_section_when_none_given(monkeypatch):
    _install_context(monkeypatch)
    seen_system = {}

    async def _fake_agentic_complete(*, system, messages, tools, execute_tool, max_tokens=1200, temperature=0.3):
        seen_system["value"] = system
        return {"text": "ok", "tool_calls": []}

    monkeypatch.setattr(claude, "agentic_complete", _fake_agentic_complete)

    asyncio.run(Agent().respond(USER_ID, "hi"))

    assert "CUSTOM INSTRUCTIONS FOR THIS PROJECT" not in seen_system["value"]
