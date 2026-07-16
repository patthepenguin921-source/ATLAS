"""Coach — accountability, weekly reviews, strategy adjustment."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.agents.base import Agent
from app.core.supabase_client import eq, supabase
from app.llm import claude
from app.services import analytics, memory


class Coach(Agent):
    role = "coach"
    name = "Coach"
    persona = (
        "You are the Coach agent of Atlas. You keep the student accountable, "
        "run honest weekly reviews, celebrate real progress, and adjust study "
        "strategy based on what actually worked."
    )

    async def weekly_review(self, user_id: str, week_start: str | None = None) -> dict[str, Any]:
        if week_start:
            start = date.fromisoformat(week_start)
        else:
            today = date.today()
            start = today - timedelta(days=today.weekday())
        start_iso = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc).isoformat()
        end_iso = (datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
                   + timedelta(days=7)).isoformat()

        completed = await supabase.select(
            "assignments", columns="title,category,status,course_id",
            filters={"user_id": eq(user_id), "submitted_at": f"gte.{start_iso}",
                     "and": f"(submitted_at.lt.{end_iso})"},
        ) or []
        sessions = await supabase.select(
            "study_sessions", columns="duration_minutes,focus_rating,technique",
            filters={"user_id": eq(user_id), "started_at": f"gte.{start_iso}",
                     "and": f"(started_at.lt.{end_iso})"},
        ) or []
        snap = await analytics.snapshot(user_id)
        ctx = await memory.build_context(user_id, None, include_semantic=False)
        context_text = memory.render_context(ctx)

        prompt = f"""\
Write this student's weekly review for the week starting {start.isoformat()}.

Assignments completed this week: {[c['title'] for c in completed]}
Study sessions: {len(sessions)}, total minutes: {sum((s.get('duration_minutes') or 0) for s in sessions)}
Analytics snapshot: {snap}

Return JSON:
{{
  "accomplishments": "what got done, honestly",
  "grade_changes": {{"summary": "..."}},
  "knowledge_gained": ["..."],
  "knowledge_weakening": ["concepts slipping / due for review"],
  "productivity": {{"summary": "...", "avg_focus": <number or null>}},
  "recommendations": "top strategy adjustments for next week",
  "goals": ["3-5 concrete goals for next week"],
  "narrative": "2-3 warm but honest paragraphs speaking directly to the student"
}}"""
        data = await claude.complete_json(
            system=self.system_prompt(context_text), prompt=prompt, max_tokens=2200
        )

        row = {
            "user_id": user_id, "week_start": start.isoformat(),
            "accomplishments": data.get("accomplishments"),
            "grade_changes": data.get("grade_changes", {}),
            "knowledge_gained": data.get("knowledge_gained", []),
            "knowledge_weakening": data.get("knowledge_weakening", []),
            "productivity": data.get("productivity", {}),
            "recommendations": data.get("recommendations"),
            "goals": data.get("goals", []),
            "narrative": data.get("narrative"),
        }
        saved = await supabase.insert("weekly_reviews", row, upsert=True, on_conflict="user_id,week_start")
        return saved[0] if saved else row
