"""Chat scope classifier -- auto-sorts a conversation into the class it's
actually about, so it shows up in that class's own section of the sidebar
without the student having to file it by hand.

Distinct from MemoryKeeper (durable facts about the student) and the
ConversationSummarizer (compressing a long thread): this looks at one
exchange and decides which single class, if any, the conversation belongs
to. See `app.routers.agents.chat`, which calls this on every turn of a
conversation that has no explicit `course_id` on the request (the student
wasn't on a class-scoped page) -- but only ever *acts* once, since a
conversation that already has a `course_id` -- set explicitly or by an
earlier pass of this same function -- is never reconsidered, so a manual
re-scope from the sidebar always sticks.

Two ways a turn gets sorted:
1. Direct mention -- one of the student's real course names appears
   verbatim in the exchange, either their own message (e.g. "what's due in
   AP Biology") or Atlas's reply (a grounded answer routinely names the
   class even when the student's own phrasing didn't). This is the literal
   "a course got mentioned" case, checked on *every* unscoped turn,
   deterministically and without an LLM call.
2. Model judgment -- for a turn that doesn't name a class directly but
   still clearly reads as belonging to one (e.g. "what's due in bio" for a
   student with only one science class), asked only once, on a brand-new
   conversation's first turn (see `allow_llm_fallback`) -- repeating a
   speculative judgment call on every later turn of a genuinely general
   conversation would just burn model calls for no benefit.
"""
from __future__ import annotations

import re

from app.core.supabase_client import eq, supabase
from app.llm import claude


def _direct_mention(user_message: str, reply: str, courses: list[dict]) -> dict | None:
    """A course whose real name appears as a whole phrase in either side of
    the exchange -- the student's own words, or Atlas's reply (grounded
    answers routinely name the class even when the student's own phrasing
    was vague, e.g. "what's due this week" answered with "your AP Biology
    lab report"). Word-boundary matched so e.g. a course named "Art"
    doesn't fire on "start" or "smart". Returns None unless exactly one
    course matches (multiple direct mentions means the turn isn't actually
    about just one class, so it's left for the model fallback -- or
    unscoped -- rather than guessed)."""
    low = f"{user_message}\n{reply}".lower()
    matches = []
    for c in courses:
        name = (c.get("name") or "").strip()
        if name and re.search(rf"\b{re.escape(name.lower())}\b", low):
            matches.append(c)
    return matches[0] if len(matches) == 1 else None


async def maybe_classify_scope(
    user_id: str, conversation_id: str, user_message: str, reply: str,
    *, allow_llm_fallback: bool = True,
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

        direct = _direct_mention(user_message, reply, courses)
        if direct:
            await supabase.update(
                "conversations", {"course_id": direct["id"]},
                filters={"user_id": eq(user_id), "id": eq(conversation_id)},
            )
            return

        if not allow_llm_fallback:
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
