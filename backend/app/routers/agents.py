"""Agent endpoints — grounded chat + specialized agent actions."""
from __future__ import annotations

from fastapi import APIRouter, Depends

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
    await supabase.insert("messages", [
        {"user_id": user_id, "conversation_id": conv_id, "role": "user", "content": user_msg},
        {"user_id": user_id, "conversation_id": conv_id, "role": "assistant",
         "content": reply, "context_used": context_used},
    ])
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
async def conversations(user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "conversations", filters={"user_id": eq(user.id)},
        order="updated_at.desc", limit=50,
    )


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "messages", columns="role,content,created_at,context_used",
        filters={"user_id": eq(user.id), "conversation_id": eq(conversation_id)},
        order="created_at.asc", limit=200,
    )
