"""ConversationSummarizer — keeps a rolling summary of a long conversation.

Sibling of MemoryKeeper: MemoryKeeper extracts durable facts about the
student as a person; this compresses the *conversation itself* so a long
thread's earlier turns aren't silently lost once they age out of
`app.routers.agents.chat`'s history window (only the most recent
`MAX_HISTORY_MESSAGES` turns are ever sent to the agent as raw history --
context-window and cost reasons). The summary stands in for whatever's been
dropped, injected into the system prompt by `app.agents.base.Agent.respond`
(see `conversation_summary` there) rather than mixed into the message list,
so it's never confused with an actual turn.

Best-effort and non-blocking to the chat turn's own success, same pattern as
MemoryKeeper -- a failure here should never break the reply the student is
waiting on.
"""
from __future__ import annotations

from app.core.supabase_client import eq, supabase
from app.llm import claude

# Below this many stored turns, the full history still fits inside
# MAX_HISTORY_MESSAGES -- nothing has been dropped yet, so there's nothing
# worth summarizing.
SUMMARY_TRIGGER_COUNT = 20
# Re-summarize once this many *new* messages have landed since the last
# pass, rather than on every single turn -- each pass is a model call.
SUMMARY_REFRESH_EVERY = 10


async def get_summary(user_id: str, conversation_id: str) -> str | None:
    rows = await supabase.select(
        "conversations", columns="summary",
        filters={"user_id": eq(user_id), "id": eq(conversation_id)}, limit=1,
    )
    return rows[0].get("summary") if rows else None


async def maybe_update_summary(user_id: str, conversation_id: str) -> None:
    """Best-effort: refresh `conversations.summary` if enough new turns have
    piled up since the last pass. Swallows all errors -- called after a chat
    turn has already been persisted and replied to, so nothing here should
    ever surface to the student."""
    from app.routers.agents import MAX_HISTORY_MESSAGES  # local import: avoids a circular import at load time

    try:
        conv_rows = await supabase.select(
            "conversations", columns="summary,summary_through_count",
            filters={"user_id": eq(user_id), "id": eq(conversation_id)}, limit=1,
        )
        if not conv_rows:
            return
        conv = conv_rows[0]
        messages = await supabase.select(
            "messages", columns="role,content",
            filters={"user_id": eq(user_id), "conversation_id": eq(conversation_id),
                     "role": "in.(user,assistant)"},
            order="created_at.asc", limit=500,
        ) or []
        total = len(messages)
        if total < SUMMARY_TRIGGER_COUNT:
            return
        already = conv.get("summary_through_count") or 0
        if total - already < SUMMARY_REFRESH_EVERY:
            return

        # Summarize everything except the tail `chat()` still sends verbatim,
        # so the summary and the raw history it's paired with never overlap.
        to_summarize = messages[: max(0, total - MAX_HISTORY_MESSAGES)]
        if not to_summarize:
            return

        transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in to_summarize)
        prior = (
            f"\n\nPRIOR SUMMARY (extend/replace it, don't just append to it):\n{conv['summary']}"
            if conv.get("summary") else ""
        )
        prompt = (
            "Summarize this student's earlier conversation with Atlas into a "
            "compact brief a future turn can use as context. Keep concrete "
            "facts -- topics discussed, decisions made, numbers/dates "
            "mentioned, anything the student asked Atlas to remember or do "
            "-- and drop small talk and back-and-forth phrasing. 150 words "
            f"or fewer.{prior}\n\nTRANSCRIPT:\n{transcript}"
        )
        summary = await claude.complete(
            system=(
                "You compress conversation history into a short, factual brief for "
                "another AI agent to read. Output only the brief itself, no preamble."
            ),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            fast=True,
        )
        summary = summary.strip()
        if not summary:
            return
        await supabase.update(
            "conversations",
            {"summary": summary, "summary_through_count": total},
            filters={"user_id": eq(user_id), "id": eq(conversation_id)},
        )
    except Exception:
        pass
