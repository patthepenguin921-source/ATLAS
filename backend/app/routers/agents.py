"""Agent endpoints — grounded chat + specialized agent actions."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.agents import get_agent
from app.agents.registry import Analyst, Coach, Planner, Tutor
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import (AnalyzeRequest, ChatRequest, ExplainRequest, PlanRequest,
                         QuizRequest, ReviewRequest)

router = APIRouter(prefix="/agents", tags=["agents"])


async def _persist_turn(user_id: str, conv_id: str | None, agent: str, user_msg: str,
                        reply: str, context_used: dict) -> str:
    if not conv_id:
        conv = await supabase.insert(
            "conversations",
            {"user_id": user_id, "agent": agent, "title": user_msg[:60]},
        )
        conv_id = conv[0]["id"]
    # PostgREST rejects a bulk insert whose objects don't share the exact same
    # set of keys (PGRST102 "All object keys must match"), so both rows must
    # carry context_used even though only the assistant turn has real data.
    await supabase.insert("messages", [
        {"user_id": user_id, "conversation_id": conv_id, "role": "user",
         "content": user_msg, "context_used": {}},
        {"user_id": user_id, "conversation_id": conv_id, "role": "assistant",
         "content": reply, "context_used": context_used},
    ])
    # bump the conversation so recent chats sort to the top of the sidebar
    await supabase.update(
        "conversations",
        {"updated_at": datetime.now(timezone.utc).isoformat()},
        filters={"id": eq(conv_id)},
    )
    return conv_id


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    agent = get_agent(body.agent)
    history: list[dict[str, str]] = []
    if body.conversation_id:
        prior = await supabase.select(
            "messages", columns="role,content",
            filters={"user_id": eq(user.id), "conversation_id": eq(body.conversation_id),
                     "role": "in.(user,assistant)"},
            order="created_at.asc", limit=20,
        ) or []
        history = [{"role": m["role"], "content": m["content"]} for m in prior]

    result = await agent.respond(
        user.id, body.message, history=history, include_semantic=body.include_semantic
    )
    conv_id = await _persist_turn(
        user.id, body.conversation_id, body.agent, body.message,
        result["reply"], result.get("context_used", {}),
    )
    return {**result, "conversation_id": conv_id}


@router.post("/planner/daily-plan")
async def daily_plan(body: PlanRequest, user: CurrentUser = Depends(get_current_user)):
    return await Planner().generate_daily_plan(user.id, body.plan_date, body.available_minutes)


@router.post("/tutor/explain")
async def explain(body: ExplainRequest, user: CurrentUser = Depends(get_current_user)):
    return await Tutor().explain(user.id, body.topic, depth=body.depth)


@router.post("/tutor/quiz")
async def quiz(body: QuizRequest, user: CurrentUser = Depends(get_current_user)):
    return await Tutor().quiz(user.id, body.topic, body.num_questions)


@router.post("/analyst/analyze")
async def analyze(body: AnalyzeRequest, user: CurrentUser = Depends(get_current_user)):
    return await Analyst().analyze(user.id, body.question)


@router.post("/coach/weekly-review")
async def weekly_review(body: ReviewRequest, user: CurrentUser = Depends(get_current_user)):
    return await Coach().weekly_review(user.id, body.week_start)


@router.get("/conversations")
async def conversations(
    archived: bool | None = None,
    project_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    filters: dict[str, str] = {"user_id": eq(user.id)}
    if archived is not None:
        filters["archived"] = f"eq.{str(archived).lower()}"
    if project_id:
        filters["project_id"] = eq(project_id)
    return await supabase.select(
        "conversations",
        columns="id,title,agent,project_id,tags,archived,created_at,updated_at",
        filters=filters, order="updated_at.desc", limit=200,
    )


# Whitelisted fields a client may change on one of its conversations.
_CONV_WRITABLE = {"title", "project_id", "tags", "archived"}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str, body: dict, user: CurrentUser = Depends(get_current_user)
):
    payload = {k: v for k, v in body.items() if k in _CONV_WRITABLE}
    if not payload:
        raise HTTPException(400, "No writable fields provided")
    updated = await supabase.update(
        "conversations", payload,
        filters={"user_id": eq(user.id), "id": eq(conversation_id)},
    )
    if not updated:
        raise HTTPException(404, "Not found")
    return updated[0]


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    # messages cascade via the conversation_id foreign key.
    await supabase.delete(
        "conversations", filters={"user_id": eq(user.id), "id": eq(conversation_id)}
    )
    return None


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "messages", columns="role,content,created_at,context_used",
        filters={"user_id": eq(user.id), "conversation_id": eq(conversation_id)},
        order="created_at.asc", limit=200,
    )
