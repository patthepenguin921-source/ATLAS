"""Analyst — studies performance, finds trends, predicts weaknesses."""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.llm import claude
from app.services import analytics, memory


class Analyst(Agent):
    role = "analyst"
    name = "Analyst"
    persona = (
        "You are the Analyst agent of Atlas. You find patterns the student "
        "cannot see: grade trajectories, retention decay, recurring mistakes, "
        "productivity windows, and risks to future performance."
    )

    async def analyze(self, user_id: str, question: str | None = None) -> dict[str, Any]:
        snap = await analytics.snapshot(user_id)
        ctx = await memory.build_context(user_id, question, include_semantic=False)
        context_text = memory.render_context(ctx)
        prompt = f"""\
Here are computed analytics for the student:
{snap}

{('Focus your analysis on this question: ' + question) if question else
 'Give a concise performance analysis.'}

Return JSON:
{{
  "headline": "the single most important insight right now",
  "trends": ["notable grade/retention/productivity trends"],
  "risks": ["specific upcoming risks with the reason"],
  "strengths": ["what is going well"],
  "recommendations": ["3-5 prioritized, specific actions"]
}}"""
        data = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=1800
        )
        return {"agent": self.role, "analytics": snap, "analysis": data}
