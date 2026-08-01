"""Agent endpoints — grounded chat + specialized agent actions."""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.agents import chat_scope, get_agent, tools
from app.agents.memory_keeper import MemoryKeeper
from app.agents.registry import Analyst, Coach, Planner, Tutor
from app.agents.summarizer import get_summary, maybe_update_summary
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import (ActionConfirmRequest, AnalyzeRequest, ChatRequest, ExplainRequest,
                         MessageFeedbackRequest, PlanRequest, QuizRequest, RegenerateRequest,
                         ReviewRequest)
from app.services import schedule

router = APIRouter(prefix="/agents", tags=["agents"])

# How many of a conversation's most recent turns `chat()` sends an agent as
# raw history. Kept as a module constant (rather than a literal in the query
# below) because `app.agents.summarizer` needs the exact same number to know
# which turns it's responsible for summarizing vs. which `chat()` already
# sends verbatim.
MAX_HISTORY_MESSAGES = 20


async def _persist_turn(user_id: str, conv_id: str | None, agent: str, user_msg: str,
                        reply: str, context_used: dict, *, course_id: str | None = None,
                        ) -> tuple[str, str | None]:
    if not conv_id:
        # `course_id` is whatever class scope was active when this brand-new
        # conversation started (see ChatRequest.course_id) -- the same
        # auto-sort a conversation started from a class-scoped page always
        # gets. One started unscoped (course_id None here) may still pick up
        # a scope shortly after, via chat()'s best-effort call to
        # `app.agents.chat_scope.maybe_classify_scope`.
        conv = await supabase.insert(
            "conversations",
            {"user_id": user_id, "agent": agent, "title": user_msg[:60], "course_id": course_id},
        )
        conv_id = conv[0]["id"]
    # PostgREST rejects a bulk insert whose objects don't share the exact same
    # set of keys (PGRST102 "All object keys must match"), so both rows must
    # carry context_used even though only the assistant turn has real data.
    inserted = await supabase.insert("messages", [
        {"user_id": user_id, "conversation_id": conv_id, "role": "user",
         "content": user_msg, "context_used": {}},
        {"user_id": user_id, "conversation_id": conv_id, "role": "assistant",
         "content": reply, "context_used": context_used},
    ])
    assistant_message_id = inserted[1]["id"] if inserted and len(inserted) > 1 else None
    # bump the conversation so recent chats sort to the top of the sidebar
    await supabase.update(
        "conversations",
        {"updated_at": datetime.now(timezone.utc).isoformat()},
        filters={"id": eq(conv_id)},
    )
    return conv_id, assistant_message_id


async def _get_project_instructions(user_id: str, conversation_id: str | None) -> str | None:
    """Custom instructions for the project a conversation is filed under
    (see `chat_projects.instructions`, migration 0027) -- a brand-new
    conversation has no project yet (that only happens after creation, via
    the sidebar's "Move to project"), so this is always None for one."""
    if not conversation_id:
        return None
    conv_rows = await supabase.select(
        "conversations", columns="project_id",
        filters={"user_id": eq(user_id), "id": eq(conversation_id)}, limit=1,
    )
    project_id = conv_rows[0].get("project_id") if conv_rows else None
    if not project_id:
        return None
    proj_rows = await supabase.select(
        "chat_projects", columns="instructions",
        filters={"user_id": eq(user_id), "id": eq(project_id)}, limit=1,
    )
    return proj_rows[0].get("instructions") if proj_rows else None


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    agent = get_agent(body.agent)
    history: list[dict[str, str]] = []
    conversation_summary: str | None = None
    if body.conversation_id:
        prior = await supabase.select(
            "messages", columns="role,content",
            filters={"user_id": eq(user.id), "conversation_id": eq(body.conversation_id),
                     "role": "in.(user,assistant)"},
            order="created_at.asc", limit=MAX_HISTORY_MESSAGES,
        ) or []
        history = [{"role": m["role"], "content": m["content"]} for m in prior]
        conversation_summary = await get_summary(user.id, body.conversation_id)
    project_instructions = await _get_project_instructions(user.id, body.conversation_id)

    result = await agent.respond(
        user.id, body.message, history=history, include_semantic=body.include_semantic,
        attachment_text=body.attachment_text, attachment_filename=body.attachment_filename,
        course_id=body.course_id, folder_id=body.folder_id,
        conversation_summary=conversation_summary, project_instructions=project_instructions,
    )
    is_new_and_unscoped = not body.conversation_id and not body.course_id
    conv_id, assistant_message_id = await _persist_turn(
        user.id, body.conversation_id, body.agent, body.message,
        result["reply"], result.get("context_used", {}), course_id=body.course_id,
    )
    try:
        await MemoryKeeper().extract_facts(
            user.id, body.message, result["reply"], conversation_id=conv_id
        )
    except Exception:
        pass  # learning long-term facts is best-effort; never break the chat turn
    try:
        await maybe_update_summary(user.id, conv_id)
    except Exception:
        pass  # rolling summary refresh is best-effort; never break the chat turn
    if is_new_and_unscoped:
        try:
            await chat_scope.maybe_classify_scope(user.id, conv_id, body.message, result["reply"])
        except Exception:
            pass  # auto-sorting a chat into a class is best-effort; never break the chat turn
    return {**result, "conversation_id": conv_id, "message_id": assistant_message_id}


@router.post("/regenerate")
async def regenerate(body: RegenerateRequest, user: CurrentUser = Depends(get_current_user)):
    """Re-runs Atlas's most recent reply in a conversation -- the rejected
    attempt is replaced in place (its row is deleted, a fresh one inserted)
    rather than appended, so reopening the conversation later shows one
    reply per turn, not the original sitting above its replacement. Unlike
    `chat()`, no new user message is inserted -- the student didn't say
    anything new, so nothing new needs remembering either (MemoryKeeper /
    the rolling summarizer are both skipped here on purpose)."""
    all_msgs = await supabase.select(
        "messages", columns="id,role,content",
        filters={"user_id": eq(user.id), "conversation_id": eq(body.conversation_id),
                 "role": "in.(user,assistant)"},
        order="created_at.asc", limit=500,
    ) or []
    if len(all_msgs) < 2 or all_msgs[-1]["role"] != "assistant" or all_msgs[-2]["role"] != "user":
        raise HTTPException(400, "Nothing to regenerate.")
    last_assistant, last_user = all_msgs[-1], all_msgs[-2]
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in all_msgs[:-2]
    ][-MAX_HISTORY_MESSAGES:]
    conversation_summary = await get_summary(user.id, body.conversation_id)
    project_instructions = await _get_project_instructions(user.id, body.conversation_id)

    agent = get_agent(body.agent)
    result = await agent.respond(
        user.id, last_user["content"], history=history,
        course_id=body.course_id, folder_id=body.folder_id,
        conversation_summary=conversation_summary, project_instructions=project_instructions,
    )
    await supabase.delete("messages", filters={"user_id": eq(user.id), "id": eq(last_assistant["id"])})
    inserted = await supabase.insert("messages", {
        "user_id": user.id, "conversation_id": body.conversation_id, "role": "assistant",
        "content": result["reply"], "context_used": result.get("context_used", {}),
    })
    await supabase.update(
        "conversations",
        {"updated_at": datetime.now(timezone.utc).isoformat()},
        filters={"id": eq(body.conversation_id)},
    )
    return {
        **result,
        "conversation_id": body.conversation_id,
        "message_id": inserted[0]["id"] if inserted else None,
    }


@router.patch("/messages/{message_id}/feedback")
async def set_message_feedback(
    message_id: str, body: MessageFeedbackRequest, user: CurrentUser = Depends(get_current_user)
):
    """A thumbs up/down (+ optional reason) on one of Atlas's own replies --
    see `messages.feedback` (migration 0025) for why this exists: it's the
    concrete signal for improving Atlas's prompts/personas/tools over time,
    since there's no model here to fine-tune directly. `rating: null` clears
    a previously-set reaction (e.g. the student clicks the same thumb twice)."""
    updated = await supabase.update(
        "messages",
        {"feedback": body.rating, "feedback_note": body.note},
        filters={"user_id": eq(user.id), "id": eq(message_id), "role": eq("assistant")},
    )
    if not updated:
        raise HTTPException(404, "Message not found")
    return {"ok": True}


@router.post("/actions/confirm")
async def confirm_action(body: ActionConfirmRequest, user: CurrentUser = Depends(get_current_user)):
    """Actually performs a destructive action the chat agent proposed
    (`pending_action` on a `/agents/chat` reply) once the student clicks
    Confirm — see `app.agents.tools` for why this is the *only* path that
    can ever delete anything, never the chat turn that first proposed it.
    `body.name`/`body.arguments` must be exactly what that turn's
    `pending_action` contained (a resolved row id, not a fuzzy title)."""
    result = await tools.confirm_tool(user.id, body.name, body.arguments)
    reply = result.get("message") or "Something went wrong — nothing was changed."
    await _persist_turn(
        user.id, body.conversation_id, "general", f"Confirmed: {body.name}", reply, {},
    )
    return {"reply": reply, "status": result.get("status"), "conversation_id": body.conversation_id}


@router.post("/planner/daily-plan")
async def daily_plan(body: PlanRequest, user: CurrentUser = Depends(get_current_user)):
    plan_date = date_cls.fromisoformat(body.plan_date) if body.plan_date else date_cls.today()
    available_minutes = body.available_minutes
    if available_minutes is None:
        available_minutes = await schedule.get_minutes_for(user.id, plan_date)
    return await Planner().generate_daily_plan(user.id, plan_date.isoformat(), available_minutes)


@router.post("/tutor/explain")
async def explain(body: ExplainRequest, user: CurrentUser = Depends(get_current_user)):
    return await Tutor().explain(
        user.id, body.topic, depth=body.depth, course_id=body.course_id, folder_id=body.folder_id,
    )


@router.post("/tutor/quiz")
async def quiz(body: QuizRequest, user: CurrentUser = Depends(get_current_user)):
    return await Tutor().quiz(
        user.id, body.topic, body.num_questions, mode=body.mode,
        course_id=body.course_id, folder_id=body.folder_id,
    )


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
        columns="id,title,agent,project_id,course_id,pinned,tags,archived,created_at,updated_at",
        filters=filters, order="updated_at.desc", limit=200,
    )


# Whitelisted fields a client may change on one of its conversations.
# `course_id` lets the student manually re-file a chat that auto-sorted (or
# didn't sort) into the wrong class, the same way `project_id` already does.
_CONV_WRITABLE = {"title", "project_id", "course_id", "pinned", "tags", "archived"}


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
        "messages", columns="id,role,content,created_at,context_used,feedback",
        filters={"user_id": eq(user.id), "conversation_id": eq(conversation_id)},
        order="created_at.asc", limit=200,
    )


@router.get("/facts")
async def list_facts(user: CurrentUser = Depends(get_current_user)):
    """What Atlas has learned about the student from past conversations."""
    return await supabase.select(
        "user_facts", columns="id,key,value,category,updated_at",
        filters={"user_id": eq(user.id)}, order="updated_at.desc", limit=200,
    )


@router.delete("/facts/{fact_id}", status_code=204)
async def forget_fact(fact_id: str, user: CurrentUser = Depends(get_current_user)):
    await supabase.delete(
        "user_facts", filters={"user_id": eq(user.id), "id": eq(fact_id)}
    )
    return None
