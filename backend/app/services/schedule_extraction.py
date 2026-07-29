"""Detecting and extracting a class schedule from an "at a glance" document.

A teacher's "Week at a Glance"/"Unit at a Glance" document lays out what
happens in class on specific days -- topics, activities, and often
assignments due -- in whatever shape the teacher wrote it (a table, a
bulleted list, prose). Matched purely by title/filename, e.g. "Week at a
Glance", "Unit 4 - At a Glance.pdf", "Chapter 7 – AT A GLANCE" -- any of
these should be treated as a schedule document and mined for a day-by-day
`calendar_events` rundown (`kind="class"`) plus any assignments it mentions.

Shared by the Schoology materials-sync path (`app.integrations.schoology`)
and the generic upload pipeline (`app.routers.documents`) so a document
matching this pattern gets the same treatment regardless of how it arrived.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.config import settings
from app.core.supabase_client import eq, supabase
from app.llm import claude

# Only the phrase itself needs to appear somewhere in the title/filename --
# teachers and LMS exports name these documents in every shape imaginable
# ("Week at a Glance", "Unit 4 - At a Glance.pdf", "AT A GLANCE: Ch. 7",
# "at-a-glance.docx"), so this deliberately does NOT require a specific word
# (week/unit/day) immediately before it the way an earlier, stricter version
# of this regex did -- that missed plain "<something> - At a Glance"
# filenames entirely. `[\s-]+` between words also catches filenames where
# spaces were replaced with hyphens.
GLANCE_TITLE_RE = re.compile(r"at[\s-]+a[\s-]+glance", re.I)

_CATEGORY_KEYWORDS = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation",
)
_ASSIGNMENT_CATEGORY_VALUES = frozenset(_CATEGORY_KEYWORDS) | {"other"}


def is_glance_title(title: str | None) -> bool:
    """True if a document's title/filename marks it as an "at a glance"
    schedule document."""
    return bool(title and GLANCE_TITLE_RE.search(title))


def _map_category(text: str) -> str:
    low = (text or "").lower()
    for key in _CATEGORY_KEYWORDS:
        if key in low:
            return key
    return "other"


def _normalize_name(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


async def extract_schedule_from_text(title: str, text: str) -> list[dict[str, Any]]:
    """Ask the reasoning engine to read an 'at a glance' document like a
    student would and return one entry per day it actually names, each with
    the day's topic and any assignment due. No LLM configured means no
    schedule -- free-form day/date layouts have no reliable regex/DOM to
    parse, so there's no safe non-LLM fallback here."""
    if not settings.has_llm or not text.strip():
        return []
    excerpt = text.strip()[:6000]
    today = date.today().isoformat()
    prompt = f"""\
A document titled "{title}" lays out what happens in class on specific days \
(a "week at a glance" or "unit at a glance" schedule). Today's date is \
{today} -- use it only to resolve a date that's missing its year, never to \
invent a date the document doesn't state.

Read the document and list every day it actually names, with what's \
happening that day and any assignment due that day.

Document text (may be truncated):
\"\"\"
{excerpt}
\"\"\"

Return JSON with this exact shape:
{{
  "days": [
    {{
      "date": "YYYY-MM-DD -- omit this entire entry if you can't resolve an actual calendar date for it",
      "topic": "short summary of what happens this day in class",
      "assignments": [
        {{"title": "...", "due_date": "YYYY-MM-DD or null", "category": "one of homework/classwork/quiz/test/exam/project/essay/lab/discussion/presentation/reading/participation/other"}}
      ]
    }}
  ]
}}
Omit assignments with no clear title."""
    try:
        result = await claude.complete_json(
            system="You are Atlas's Archivist, precisely extracting a class schedule from a teacher's document.",
            prompt=prompt, max_tokens=1500, temperature=0.0,
        )
    except Exception:  # noqa: BLE001
        return []
    days = result.get("days") if isinstance(result, dict) else None
    if not isinstance(days, list):
        return []
    return [d for d in days if isinstance(d, dict) and d.get("date")]


async def _upsert_calendar_event(user_id: str, external_id: str, fields: dict[str, Any]) -> None:
    existing = await supabase.select(
        "calendar_events", columns="id",
        filters={"user_id": eq(user_id), "external_id": eq(external_id)}, limit=1,
    )
    payload = {**fields, "user_id": user_id, "external_id": external_id}
    if existing:
        await supabase.update("calendar_events", payload, filters={"id": eq(existing[0]["id"])})
    else:
        await supabase.insert("calendar_events", payload)


async def _upsert_assignment(user_id: str, external_id: str, source: str, fields: dict[str, Any]) -> None:
    existing = await supabase.select(
        "assignments", columns="id",
        filters={"user_id": eq(user_id), "external_id": eq(external_id),
                 "external_source": eq(source)}, limit=1,
    )
    payload = {**fields, "user_id": user_id, "external_id": external_id, "external_source": source}
    if existing:
        await supabase.update("assignments", payload, filters={"id": eq(existing[0]["id"])})
    else:
        await supabase.insert("assignments", payload)


async def apply_schedule_from_doc(
    *, user_id: str, course_id: str, title: str, text: str, source: str,
    source_document_id: str | None = None, report: dict[str, Any] | None = None,
) -> None:
    """Turn a parsed 'at a glance' schedule into `calendar_events` rows
    (`kind="class"`, so the course page can show a day-by-day view) plus any
    assignments it mentions -- teachers often only ever list an assignment's
    due date in a schedule like this, never as a proper LMS assignment.

    `source` scopes the external ids/`external_source` this writes (e.g.
    "schoology" for a materials-sync import, "manual" for a directly
    uploaded document) so two different origins never collide or stomp each
    other's rows for the same course/date."""
    days = await extract_schedule_from_text(title, text)
    for day in days:
        iso_date = day.get("date")
        if not iso_date:
            continue
        try:
            await _upsert_calendar_event(
                user_id, f"{source}:class:{course_id}:{iso_date}", {
                    "course_id": course_id, "title": day.get("topic") or title,
                    "description": day.get("topic") or None,
                    "starts_at": iso_date, "all_day": True, "kind": "class",
                    "metadata": {
                        "source_document_id": source_document_id,
                        "detected_from": "glance_doc",
                    },
                },
            )
            if report is not None:
                report["events"] += 1
        except Exception as e:  # noqa: BLE001
            if report is not None:
                report["errors"].append(f"{title} ({iso_date}): couldn't save class schedule ({e})")
        for a in (day.get("assignments") or []):
            a_title = (a.get("title") or "").strip() if isinstance(a, dict) else ""
            if not a_title:
                continue
            category = a.get("category")
            if category not in _ASSIGNMENT_CATEGORY_VALUES:
                category = _map_category(a_title)
            due = a.get("due_date") or iso_date
            ext_id = f"{source}:glance-assignment:{course_id}:{_normalize_name(a_title)}"
            try:
                await _upsert_assignment(user_id, ext_id, source, {
                    "course_id": course_id, "title": a_title, "category": category,
                    "due_date": due, "status": "not_started",
                    "metadata": {
                        "source_document_id": source_document_id,
                        "detected_from": "glance_doc",
                    },
                })
                if report is not None:
                    report["assignments"] += 1
            except Exception as e:  # noqa: BLE001
                if report is not None:
                    report["errors"].append(
                        f"{a_title}: detected as assignment but couldn't save it ({e})"
                    )
