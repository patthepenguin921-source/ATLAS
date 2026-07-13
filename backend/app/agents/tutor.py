"""Tutor — explains concepts, builds quizzes, drives active recall + spaced rep."""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.llm import claude
from app.services import memory


class Tutor(Agent):
    role = "tutor"
    name = "Tutor"
    persona = (
        "You are the Tutor agent of Atlas. You teach for durable understanding "
        "using active recall and spaced repetition. You adapt explanations to "
        "the student's known misconceptions and prior mistakes."
    )

    async def explain(self, user_id: str, topic: str, *, depth: str = "standard") -> dict[str, Any]:
        ctx = await memory.build_context(user_id, topic, include_semantic=True)
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

    async def quiz(self, user_id: str, topic: str, num_questions: int = 5) -> dict[str, Any]:
        ctx = await memory.build_context(user_id, topic, include_semantic=True)
        context_text = memory.render_context(ctx)
        prompt = f"""\
Create a {num_questions}-question active-recall quiz on: {topic}.
Mix recall and application. Prefer material from my own documents/context when
available. Return JSON:
{{
  "topic": "{topic}",
  "questions": [
    {{"q": "...", "type": "short_answer|multiple_choice",
      "choices": ["A","B","C","D"] or null,
      "answer": "...", "explanation": "...", "concept": "..."}}
  ]
}}"""
        data = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=2000
        )
        return {"agent": self.role, **data}
