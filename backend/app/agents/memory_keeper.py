"""MemoryKeeper — extracts durable facts about the student from chat turns.

Distinct from the Archivist (which enriches uploaded documents): this looks at
one conversation exchange and pulls out anything worth remembering about the
student *as a person* going forward — preferences, goals, constraints,
recurring context — so future turns don't start from zero.
"""
from __future__ import annotations

from app.agents.base import Agent
from app.core.supabase_client import eq, supabase
from app.llm import claude

VALID_CATEGORIES = {"preference", "goal", "constraint", "context"}

# A cheap, pre-LLM gate: most turns carry nothing durable (small talk, a
# one-off homework question, "thanks"), so don't spend a model call finding
# that out. Below this length there's essentially never enough in a single
# exchange to state a genuinely new, durable fact.
MIN_MESSAGE_CHARS = 40


class MemoryKeeper(Agent):
    role = "memory_keeper"
    name = "Memory Keeper"
    persona = (
        "You are the Memory Keeper agent of Atlas. You read a single chat "
        "exchange and decide what, if anything, is worth remembering about "
        "the student long-term. You are deliberately strict: most exchanges "
        "contain nothing worth keeping, and you are expected to return an "
        "empty list far more often than not."
    )

    async def extract_facts(
        self, user_id: str, user_message: str, reply: str, *, conversation_id: str | None = None,
    ) -> list[str]:
        """Best-effort: pull durable facts/preferences out of one turn and store them."""
        if len(user_message.strip()) < MIN_MESSAGE_CHARS:
            return []

        existing = await supabase.select(
            "user_facts", columns="key,value",
            filters={"user_id": eq(user_id)}, order="updated_at.desc", limit=50,
        ) or []
        existing_block = (
            "\n".join(f"- {f['key']}: {f['value']}" for f in existing)
            if existing else "(none yet)"
        )

        prompt = f"""\
Read this one exchange between a student and Atlas (their academic
assistant). Decide whether it contains a durable fact about THIS STUDENT AS
A PERSON worth remembering across future, unrelated conversations.

Extract a fact ONLY if ALL of these hold:
- It is genuinely durable — true for weeks/months, not just this
  conversation (a stable preference, goal, constraint, or life context).
- It is about the student specifically, not general knowledge, trivia, or
  facts about a course/exam/subject that happen to come up (e.g. "the AP
  Research exam has three components" is NOT a fact about the student —
  discard it even if it was mentioned).
- It is not already captured by an existing fact below, even worded
  differently — if it restates or barely refines one you already have,
  discard it rather than storing a near-duplicate.
- It is not obvious from academic records already available to Atlas
  (grades, courses, assignments) or from a single passing mention with no
  real signal (asking about a topic once is not "interested in" it).
- It is specific enough to be useful — reject vague restatements like
  "values critical thinking" or "prefers good information" that don't
  actually help a future conversation.

When genuinely uncertain, do NOT extract it. Returning an empty list is the
expected, common outcome — do not force a fact out of a turn that doesn't
have one.

EXISTING FACTS ALREADY STORED FOR THIS STUDENT:
{existing_block}

Return JSON: {{"facts": [{{"key": "short_snake_case_label", "value": "the fact, in natural language", "category": "one of: preference, goal, constraint, context"}}]}}
Return at most 2 facts. The "category" field must be exactly one of those
four words — never the list itself.

STUDENT: {user_message}

ATLAS: {reply}"""
        try:
            data = await claude.complete_json(
                system=self.persona, prompt=prompt, max_tokens=500, fast=True
            )
        except Exception:
            return []

        stored: list[str] = []
        for fact in (data.get("facts") or [])[:2]:
            key = (fact.get("key") or "").strip().lower().replace(" ", "_")
            value = (fact.get("value") or "").strip()
            category = (fact.get("category") or "").strip().lower()
            if not key or not value or category not in VALID_CATEGORIES:
                continue
            await supabase.insert(
                "user_facts",
                {
                    "user_id": user_id, "key": key, "value": value,
                    "category": category,
                    "source_conversation_id": conversation_id,
                },
                upsert=True,
                on_conflict="user_id,key",
            )
            stored.append(key)
        return stored
