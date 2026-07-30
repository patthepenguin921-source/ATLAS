"""`POST /agents/actions/confirm` -- the only path that can actually
perform a destructive action the chat agent proposed. Exercises the
route function directly (same pattern as test_document_resync.py).
"""
from __future__ import annotations

import asyncio
import uuid

import app.routers.agents as agents_module
from app.core.security import CurrentUser
from app.schemas import ActionConfirmRequest

USER_ID = str(uuid.uuid4())
CONV_ID = str(uuid.uuid4())
USER = CurrentUser(id=USER_ID, email="student@example.com")


def test_confirm_action_executes_and_persists_the_reply(monkeypatch):
    async def _fake_confirm_tool(user_id, name, arguments):
        assert user_id == USER_ID
        assert name == "delete_assignment"
        assert arguments == {"id": "a1"}
        return {"status": "done", "message": 'Deleted "Old Homework".'}

    persisted = []

    async def _fake_persist_turn(user_id, conv_id, agent, user_msg, reply, context_used):
        persisted.append((conv_id, user_msg, reply))
        return conv_id

    monkeypatch.setattr(agents_module.tools, "confirm_tool", _fake_confirm_tool)
    monkeypatch.setattr(agents_module, "_persist_turn", _fake_persist_turn)

    result = asyncio.run(agents_module.confirm_action(
        ActionConfirmRequest(name="delete_assignment", arguments={"id": "a1"}, conversation_id=CONV_ID),
        user=USER,
    ))

    assert result == {"reply": 'Deleted "Old Homework".', "status": "done", "conversation_id": CONV_ID}
    assert persisted == [(CONV_ID, "Confirmed: delete_assignment", 'Deleted "Old Homework".')]


def test_confirm_action_reports_failure_without_crashing(monkeypatch):
    async def _fake_confirm_tool(user_id, name, arguments):
        return {"status": "error", "message": "That item no longer exists (maybe already deleted)."}

    async def _fake_persist_turn(*a, **k):
        return CONV_ID

    monkeypatch.setattr(agents_module.tools, "confirm_tool", _fake_confirm_tool)
    monkeypatch.setattr(agents_module, "_persist_turn", _fake_persist_turn)

    result = asyncio.run(agents_module.confirm_action(
        ActionConfirmRequest(name="delete_assignment", arguments={"id": "gone"}, conversation_id=CONV_ID),
        user=USER,
    ))

    assert result["status"] == "error"
    assert "no longer exists" in result["reply"]
