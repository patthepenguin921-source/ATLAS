"""Practice quiz/test history -- persists what `Tutor.quiz` generates (see
`app.agents.tutor.Tutor.quiz`) so past practice is browsable instead of
vanishing the moment the page reloads (see `practice_sessions`, migration
0029). Written to by both `POST /agents/tutor/quiz` and the chat agent's
`generate_practice_quiz` tool, read back by `app.routers.practice`.
"""
from __future__ import annotations

from typing import Any

from app.core.supabase_client import eq, supabase

_MODES = ("quiz", "test")
_SOURCES = ("materials", "internet")


async def save_session(
    user_id: str, *, topic: str, mode: str, source: str, questions: list[dict[str, Any]],
    course_id: str | None = None, folder_id: str | None = None,
) -> dict[str, Any]:
    row = {
        "user_id": user_id, "course_id": course_id, "folder_id": folder_id,
        "topic": topic,
        "mode": mode if mode in _MODES else "quiz",
        "source": source if source in _SOURCES else "materials",
        "questions": questions,
    }
    created = await supabase.insert("practice_sessions", row)
    return created[0] if created else row


async def list_sessions(
    user_id: str, *, course_id: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    filters: dict[str, str] = {"user_id": eq(user_id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    return await supabase.select(
        "practice_sessions",
        columns="id,course_id,folder_id,topic,mode,source,score,created_at",
        filters=filters, order="created_at.desc", limit=limit,
    ) or []


async def get_session(user_id: str, session_id: str) -> dict[str, Any] | None:
    rows = await supabase.select(
        "practice_sessions", filters={"user_id": eq(user_id), "id": eq(session_id)}, limit=1,
    )
    return rows[0] if rows else None


async def delete_session(user_id: str, session_id: str) -> None:
    await supabase.delete(
        "practice_sessions", filters={"user_id": eq(user_id), "id": eq(session_id)}
    )


async def record_score(user_id: str, session_id: str, score: float) -> dict[str, Any] | None:
    updated = await supabase.update(
        "practice_sessions", {"score": score},
        filters={"user_id": eq(user_id), "id": eq(session_id)},
    )
    return updated[0] if updated else None
