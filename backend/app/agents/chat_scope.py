"""Chat scope classifier -- auto-sorts a brand-new, unscoped conversation
into the class it's actually about, so it shows up in that class's own
section of the sidebar without the student having to file it by hand.

Distinct from MemoryKeeper (durable facts about the student) and the
ConversationSummarizer (compressing a long thread): this looks at one fresh
exchange and decides which single class, if any, the whole conversation
belongs to. Only ever runs once, right after a conversation's first turn --
see `app.routers.agents.chat`, which calls this only when the request itself
carried no explicit `course_id` (the student wasn't already on a class-
scoped page) and the conversation was just created. A conversation that
already has a `course_id` -- set explicitly or by a prior pass of this same
function -- is never reconsidered, so a manual re-scope from the sidebar
always sticks.
"""
from __future__ import annotations

from app.core.supabase_client import eq, supabase
from app.llm import claude


async def maybe_classify_scope(
    user_id: str, conversation_id: str, user_message: str, reply: str,
) -> None:
    """Best-effort: swallows all errors. Called after the turn has already
    been persisted and replied to, so nothing here should ever surface to
    the student -- a missed classification just leaves the chat under
    "General," which is always a safe fallback, never a wrong answer."""
    try:
        conv_rows = await supabase.select(
            "conversations", columns="course_id",
            filters={"user_id": eq(user_id), "id": eq(conversation_id)}, limit=1,
        )
        if not conv_rows or conv_rows[0].get("course_id"):
            return  # already scoped -- never second-guess it

        courses = await supabase.select(
            "courses", columns="id,name",
            filters={"user_id": eq(user_id), "is_active": eq("true")},
        ) or []
        if not courses:
            return

        names_block = "\n".join(f"- {c['name']}" for c in courses)
        prompt = f"""\
Here is one exchange between a student and Atlas, their academic assistant:

STUDENT: {user_message}

ATLAS: {reply}

Here are the student's actual classes:
{names_block}

Does this exchange clearly belong to exactly ONE of these classes -- not a
general question, not something that could apply across several classes?
Only answer with a class if it's unambiguous; a single passing mention of a
subject is not enough on its own. Return JSON:
{{"course_name": "<exact name from the list above, or null>"}}"""
        data = await claude.complete_json(
            system=(
                "You classify one chat exchange as belonging to exactly one of the "
                "student's real classes, or none. You are deliberately conservative -- "
                "most exchanges don't clearly belong to just one class."
            ),
            prompt=prompt, max_tokens=200, fast=True,
        )
        name = (data.get("course_name") or "").strip()
        if not name:
            return
        match = next((c for c in courses if (c.get("name") or "").strip().lower() == name.lower()), None)
        if not match:
            return
        await supabase.update(
            "conversations", {"course_id": match["id"]},
            filters={"user_id": eq(user_id), "id": eq(conversation_id)},
        )
    except Exception:
        pass
