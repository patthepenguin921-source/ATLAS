"""MemoryKeeper — extracts durable facts about the student from chat turns.

Distinct from the Archivist (which enriches uploaded documents): this looks at
one conversation exchange and pulls out anything worth remembering about the
student *as a person* going forward — preferences, goals, constraints,
recurring context — so future turns don't start from zero.
"""
from __future__ import annotations

from app.agents.base import Agent
from app.core.supabase_client import supabase
from app.llm import claude


class MemoryKeeper(Agent):
    role = "memory_keeper"
    name = "Memory Keeper"
    persona = (
        "You are the Memory Keeper agent of Atlas. You read a single chat "
        "exchange and decide what, if anything, is worth remembering about "
        "the student long-term."
    )

    async def extract_facts(
        self, user_id: str, user_message: str, reply: str, *, conversation_id: str | None = None,
    ) -> list[str]:
        """Best-effort: pull durable facts/preferences out of one turn and store them."""
        prompt = f"""\
Read this one exchange between a student and Atlas (their academic
assistant). Extract only durable facts worth remembering in future
conversations — stable preferences, goals, constraints, or personal context
(e.g. "prefers visual explanations", "has soccer practice until 7pm on
weeknights", "is aiming for a 4.0 this semester"). Do NOT extract one-off
question content, homework specifics, or anything already obvious from
academic records (grades, courses, assignments).

If there is nothing durable worth remembering, return an empty list.

Return JSON: {{"facts": [{{"key": "short_snake_case_label", "value": "the fact, in natural language", "category": "preference|goal|constraint|context"}}]}}
Return at most 5 facts.

STUDENT: {user_message}

ATLAS: {reply}"""
        try:
            data = await claude.complete_json(
                system=self.persona, prompt=prompt, max_tokens=500, fast=True
            )
        except Exception:
            return []

        stored: list[str] = []
        for fact in (data.get("facts") or [])[:5]:
            key = (fact.get("key") or "").strip().lower().replace(" ", "_")
            value = (fact.get("value") or "").strip()
            if not key or not value:
                continue
            await supabase.insert(
                "user_facts",
                {
                    "user_id": user_id, "key": key, "value": value,
                    "category": fact.get("category"),
                    "source_conversation_id": conversation_id,
                },
                upsert=True,
                on_conflict="user_id,key",
            )
            stored.append(key)
        return stored
