"""Planner — figures out what needs to get done and by when, kills decision fatigue.

Atlas doesn't own the calendar: it never assigns clock times. It surfaces what's
due, breaks multi-day projects into dated chunks, and leaves the actual
scheduling (if any) to the student asking explicitly.
"""
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
        "You are the Planner agent of Atlas. You figure out what needs to get "
        "done and by when — you never assign clock times or build a schedule. "
        "You prioritize at-risk and time-sensitive work, and break any "
        "multi-day project into dated chunks spread across the days remaining "
        "before it's due."
    )

    async def generate_daily_plan(
        self, user_id: str, plan_date: str | None = None, available_minutes: int = 180
    ) -> dict[str, Any]:
        plan_date = plan_date or date_cls.today().isoformat()
        ctx = await memory.build_context(user_id, "today's plan", include_semantic=False)
        risk = await analytics.at_risk_assignments(user_id, limit=8)
        context_text = memory.render_context(ctx)

        capacity_note = (
            f"You have about {available_minutes} minutes of real study time today — this is "
            "the student's own configured availability for today (after-school activities, work, "
            "practice, etc. already accounted for), not a rough guess, so respect it strictly "
            "rather than overloading the day."
        )
        if available_minutes <= 20:
            capacity_note += (
                " That's very little time — give at most one short, high-priority item (or "
                "explicitly say there's nothing urgent enough to justify even that), don't cram "
                "a normal day's worth of tasks into it."
            )
        elif available_minutes <= 45:
            capacity_note += " That's a light day — keep it to the one or two things that matter most."

        prompt = f"""\
Plan out what needs to get done starting {plan_date}. {capacity_note} Do NOT
assign clock times or build a schedule — only figure out what needs to
happen and by when. Prioritize at-risk and time-sensitive work, and
interleave review of weak concepts.

Every task must name a REAL, SPECIFIC thing to do, using the student's own
courses/assignments/documents shown in the context above — never a vague
subject-level suggestion. For example:
- "Complete the Lab Report for AP Biology" (a real assignment), not
  "study biology"
- "Read the Unit 3 slideshow for AP Biology" (a real document from
  "Documents you have on file" in the context) when a course needs review
  time but has no assignment due soon, not "review notes"
- "Review [concept name]" only when that concept is actually listed under
  "Concepts due for review" in the context — never invent a concept name
- If nothing concrete justifies a task for a course today, leave it out
  entirely rather than manufacturing a generic placeholder.

If a single assignment is large enough that it can't reasonably be finished
in one sitting (e.g. a multi-page paper, a big project, exam prep spanning a
unit), split it into several dated chunks (e.g. "Outline", "Draft body",
"Revise + cite") spread across the days between now and its due date instead
of leaving it as one task the night before.

At-risk assignments (highest first): {[{'title': a['title'], 'risk': a['risk_score'], 'risk_level': a['risk_level'], 'days_left': a['days_left']} for a in risk]}

Return JSON with this exact shape:
{{
  "summary": "one-sentence overview",
  "priorities": ["ordered list of what matters most, today first"],
  "tasks": [
    {{"date": "YYYY-MM-DD", "task": "...", "course": "...",
      "part": "e.g. 'Part 1 of 3' if this is a chunk of a bigger project, else null",
      "due_date": "YYYY-MM-DD of the assignment's real deadline, or null",
      "estimated_minutes": <int>, "why": "..."}}
  ],
  "motivational_note": "short encouraging line"
}}"""
        plan = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=1600
        )
        tasks = plan.get("tasks", [])
        today_minutes = sum(
            t.get("estimated_minutes") or 0 for t in tasks if t.get("date") == plan_date
        )

        row = {
            "user_id": user_id,
            "plan_date": plan_date,
            "summary": plan.get("summary"),
            "blocks": tasks,
            "priorities": plan.get("priorities", []),
            "estimated_minutes": today_minutes or None,
            "motivational_note": plan.get("motivational_note"),
            "generated_by": "planner",
        }
        saved = await supabase.insert("daily_plans", row, upsert=True, on_conflict="user_id,plan_date")
        return saved[0] if saved else row
