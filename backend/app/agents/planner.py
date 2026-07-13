"""Planner — creates daily schedules, prioritizes work, kills decision fatigue."""
from __future__ import annotations

from datetime import date as date_cls
from typing import Any

from app.agents.base import Agent
from app.core.supabase_client import supabase
from app.llm import claude
from app.services import analytics, memory


class Planner(Agent):
    role = "planner"
    name = "Planner"
    persona = (
        "You are the Planner agent of Atlas. You build realistic daily study "
        "plans that balance workload, protect against procrastination, and "
        "front-load high-impact, time-sensitive work."
    )

    async def generate_daily_plan(
        self, user_id: str, plan_date: str | None = None, available_minutes: int = 180
    ) -> dict[str, Any]:
        plan_date = plan_date or date_cls.today().isoformat()
        ctx = await memory.build_context(user_id, "today's plan", include_semantic=False)
        risk = await analytics.at_risk_assignments(user_id, limit=8)
        context_text = memory.render_context(ctx)

        prompt = f"""\
Build a study plan for {plan_date} using about {available_minutes} minutes of
study time. Prioritize at-risk and time-sensitive work, interleave review of
weak concepts, and keep blocks realistic (25-50 min focus blocks with breaks).

At-risk assignments (highest first): {[{'title': a['title'], 'risk': a['risk_score'], 'days_left': a['days_left']} for a in risk]}

Return JSON with this exact shape:
{{
  "summary": "one-sentence overview",
  "priorities": ["ordered list of what matters most today"],
  "blocks": [
    {{"start": "16:00", "end": "16:45", "task": "...", "course": "...", "why": "..."}}
  ],
  "estimated_minutes": <int>,
  "motivational_note": "short encouraging line"
}}"""
        plan = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=1600
        )

        row = {
            "user_id": user_id,
            "plan_date": plan_date,
            "summary": plan.get("summary"),
            "blocks": plan.get("blocks", []),
            "priorities": plan.get("priorities", []),
            "estimated_minutes": plan.get("estimated_minutes"),
            "motivational_note": plan.get("motivational_note"),
            "generated_by": "planner",
        }
        saved = await supabase.insert("daily_plans", row, upsert=True)
        return saved[0] if saved else row
