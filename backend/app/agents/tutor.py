"""Tutor — explains concepts, builds quizzes, drives active recall + spaced rep."""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.llm import claude
from app.services import memory, web_search


class Tutor(Agent):
    role = "tutor"
    name = "Tutor"
    persona = (
        "You are the Tutor agent of Atlas. You teach for durable understanding "
        "using active recall and spaced repetition. You adapt explanations to "
        "the student's known misconceptions and prior mistakes."
    )

    async def explain(
        self, user_id: str, topic: str, *, depth: str = "standard",
        course_id: str | None = None, folder_id: str | None = None,
    ) -> dict[str, Any]:
        ctx = await memory.build_context(
            user_id, topic, include_semantic=True, course_id=course_id, folder_id=folder_id,
        )
        context_text = memory.render_context(ctx)
        style = {
            "quick": "Give a crisp 3-4 sentence explanation.",
            "standard": "Explain clearly with an intuition, a worked example, and one common pitfall.",
            "deep": "Explain thoroughly: intuition, formal definition, worked example, common pitfalls, and how it connects to related concepts.",
        }.get(depth, "Explain clearly.")
        prompt = (
            f"Teach me: {topic}\n\n{style}\n"
            "Where relevant, connect it to my own documents/notes shown in context "
            "and to mistakes I've made before. End with one active-recall question."
        )
        text = await claude.complete(
            system=self.system_prompt(context_text),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
        return {"agent": self.role, "topic": topic, "explanation": text}

    async def quiz(
        self, user_id: str, topic: str, num_questions: int = 5, *, mode: str = "quiz",
        course_id: str | None = None, folder_id: str | None = None, source: str = "materials",
    ) -> dict[str, Any]:
        """`mode="quiz"` is a quick active-recall check; `mode="test"` is a
        longer, exam-style practice test -- both share this one generator so
        "give me a quick quiz on X" and "give me a practice test on X" only
        differ in length/difficulty, not in how they're grounded.

        `source="materials"` (default) grounds questions in the student's own
        documents/notes, same as before. `source="internet"` instead pulls
        from a live web search on the topic -- for practicing something not
        covered in their own materials yet -- and is kept clearly labeled as
        such rather than blurred with "from your documents" (see
        `app.services.web_search`'s module docstring)."""
        if source == "internet":
            results, err = await web_search.search(topic, max_results=6)
            if results:
                context_text = "Web search results (NOT the student's own materials):\n\n" + "\n\n".join(
                    f"- {r.get('title') or 'Untitled'} ({r.get('url')})\n  {r.get('content') or ''}"
                    for r in results
                )
            else:
                context_text = (
                    "No web search results were available "
                    f"({err or 'unknown reason'}) -- generate from general knowledge of the "
                    "topic instead, and note in the response that this isn't grounded in a "
                    "live source."
                )
        else:
            ctx = await memory.build_context(
                user_id, topic, include_semantic=True, course_id=course_id, folder_id=folder_id,
            )
            context_text = memory.render_context(ctx)
        style = {
            "quiz": "a quick active-recall quiz -- mostly straightforward recall, one or two application questions",
            "test": (
                "a longer, exam-style practice test -- mix difficulty deliberately (recall, "
                "application, and at least one multi-step/synthesis question), the way a real "
                "unit test would, not just definition recall"
            ),
        }.get(mode, "a quiz")
        grounding = (
            "Ground questions in the web search results above -- this is explicitly practice "
            "from the open internet, not the student's own materials."
            if source == "internet" else
            "Prefer material from my own documents/context when available -- ground "
            "questions in what's actually in scope rather than the topic in the abstract."
        )
        prompt = f"""\
Create {style} with {num_questions} questions on: {topic}.
{grounding}
Return JSON:
{{
  "topic": "{topic}",
  "questions": [
    {{"q": "...", "type": "short_answer|multiple_choice",
      "choices": ["A","B","C","D"] or null,
      "answer": "...", "explanation": "...", "concept": "..."}}
  ]
}}"""
        data = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=2400
        )
        return {"agent": self.role, "mode": mode, "source": source, **data}
