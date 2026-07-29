"""Memory retrieval — assembles grounded context for the reasoning engine.

This is what makes Atlas "know more about your academic life than you
consciously remember": before Claude reasons, we pull the *relevant* slice of
structured facts (courses, deadlines, grades, weak concepts) and semantic
matches (document passages) and hand them over as context.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import eq, supabase
from app.embeddings.embedder import embed_text


async def semantic_search(
    user_id: str, query: str, *, limit: int = 8, course_id: str | None = None,
    threshold: float = 0.0,
) -> list[dict[str, Any]]:
    """Cosine-similarity search over the student's document chunks."""
    embedding = await embed_text(query)
    payload = {
        "query_embedding": embedding,
        "p_user_id": user_id,
        "match_count": limit,
        "similarity_threshold": threshold,
        "p_course_id": course_id,
    }
    return await supabase.rpc("match_document_chunks", payload) or []


async def upcoming_assignments(user_id: str, *, days: int = 14, limit: int = 25) -> list[dict]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    return await supabase.select(
        "assignments",
        columns="id,title,category,status,due_date,course_id,estimated_minutes,difficulty,points_possible",
        filters={
            "user_id": eq(user_id),
            "due_date": f"gte.{now.isoformat()}",
            "and": f"(due_date.lte.{horizon.isoformat()})",
            "status": "not.in.(graded,excused,submitted)",
        },
        order="due_date.asc",
        limit=limit,
    ) or []


async def upcoming_events(user_id: str, *, days: int = 14, limit: int = 25) -> list[dict]:
    """Upcoming `calendar_events` rows -- exams, quizzes, classes, and other
    LMS-calendar items. Distinct from `upcoming_assignments`: a test/exam is
    routinely synced as a calendar event (e.g. Schoology section-calendar
    items get `kind="exam"` or `kind="quiz"` from their title -- see
    `SchoologyProvider._sync_section`) rather than as an `assignments` row,
    so without this a student's own "when's my test" question has no
    grounding at all even though the same data already renders on their
    dashboard/calendar view (`app.routers.dashboard`). `kind` deliberately
    keeps "quiz" distinct from "exam" so a quiz never gets handed back as
    the answer to a "when's my test" question."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    return await supabase.select(
        "calendar_events",
        columns="id,title,description,starts_at,ends_at,all_day,kind,course_id",
        filters={
            "user_id": eq(user_id),
            "starts_at": f"gte.{now.isoformat()}",
            "and": f"(starts_at.lte.{horizon.isoformat()})",
        },
        order="starts_at.asc",
        limit=limit,
    ) or []


async def overdue_or_missing(user_id: str, *, limit: int = 25) -> list[dict]:
    now = datetime.now(timezone.utc)
    return await supabase.select(
        "assignments",
        columns="id,title,category,status,due_date,course_id",
        filters={
            "user_id": eq(user_id),
            "status": "in.(not_started,in_progress,missing,late)",
            "due_date": f"lt.{now.isoformat()}",
        },
        order="due_date.asc",
        limit=limit,
    ) or []


async def courses_overview(user_id: str) -> list[dict]:
    return await supabase.select(
        "courses",
        columns="id,name,code,subject,course_level,has_hn_prep_lab,has_ap_prep_lab,"
                 "current_grade,current_letter,teacher_id,is_active",
        filters={"user_id": eq(user_id)},
        order="name.asc",
    ) or []


async def recent_grades(user_id: str, *, limit: int = 15) -> list[dict]:
    return await supabase.select(
        "grades",
        columns="id,course_id,assignment_id,percentage,letter,score,points_possible,graded_at,teacher_comment",
        filters={"user_id": eq(user_id)},
        order="graded_at.desc.nullslast",
        limit=limit,
    ) or []


async def concepts_needing_review(user_id: str, *, limit: int = 15) -> list[dict]:
    """Concepts due for spaced-repetition review or estimated to be forgotten."""
    now = datetime.now(timezone.utc)
    rows = await supabase.select(
        "student_knowledge",
        columns="concept_id,mastery,retention,confidence,next_review_at,predicted_forget_at",
        filters={
            "user_id": eq(user_id),
            "or": f"(next_review_at.lte.{now.isoformat()},retention.lt.0.5)",
        },
        order="retention.asc",
        limit=limit,
    ) or []
    if not rows:
        return []
    ids = ",".join(r["concept_id"] for r in rows)
    names = await supabase.select(
        "concepts", columns="id,name,subject", filters={"id": f"in.({ids})"}
    ) or []
    name_map = {c["id"]: c for c in names}
    for r in rows:
        c = name_map.get(r["concept_id"], {})
        r["name"] = c.get("name")
        r["subject"] = c.get("subject")
    return rows


async def repeated_mistakes(user_id: str, *, limit: int = 15) -> list[dict]:
    return await supabase.select(
        "mistakes",
        columns="id,description,mistake_type,course_id,concept_id,occurred_at,resolved",
        filters={"user_id": eq(user_id), "resolved": eq("false")},
        order="occurred_at.desc",
        limit=limit,
    ) or []


async def learned_facts(user_id: str, *, limit: int = 50) -> list[dict]:
    """Durable facts/preferences learned from past conversations (see MemoryKeeper)."""
    return await supabase.select(
        "user_facts",
        columns="key,value,category",
        filters={"user_id": eq(user_id)},
        order="updated_at.desc",
        limit=limit,
    ) or []


async def available_documents(user_id: str, *, limit: int = 50) -> list[dict]:
    """Titles/summaries of every ingested document the student has.

    Semantic search only surfaces the top few *chunks* for a given query, so a
    document can exist and still never appear in `relevant_passages` if its
    embedding doesn't happen to win that ranking. Without this manifest Claude
    has no way to know a document exists at all unless the user names it
    explicitly. Listing titles/summaries up front lets Claude notice a
    relevant document and pull it in (or ask to), instead of only reacting
    when the user spells out the filename.
    """
    return await supabase.select(
        "documents",
        columns="id,title,doc_type,summary,course_id",
        filters={"user_id": eq(user_id), "ingested": eq("true")},
        order="created_at.desc",
        limit=limit,
    ) or []


async def build_context(
    user_id: str, query: str | None = None, *,
    include_semantic: bool = True, semantic_limit: int = 6,
) -> dict[str, Any]:
    """Assemble a compact, grounded snapshot of the student's academic state."""
    ctx: dict[str, Any] = {
        "courses": await courses_overview(user_id),
        "upcoming": await upcoming_assignments(user_id),
        "upcoming_events": await upcoming_events(user_id),
        "overdue": await overdue_or_missing(user_id),
        "recent_grades": await recent_grades(user_id, limit=10),
        "review_due": await concepts_needing_review(user_id, limit=10),
        "repeated_mistakes": await repeated_mistakes(user_id, limit=10),
        "documents": await available_documents(user_id),
        "learned_facts": await learned_facts(user_id),
    }
    if query and include_semantic:
        try:
            ctx["relevant_passages"] = await semantic_search(user_id, query, limit=semantic_limit)
        except Exception as e:  # semantic layer is best-effort
            ctx["relevant_passages"] = []
            ctx["semantic_error"] = str(e)
    return ctx


def render_context(ctx: dict[str, Any]) -> str:
    """Render the context dict into compact text for a Claude system prompt."""
    lines: list[str] = ["# STUDENT ACADEMIC CONTEXT (retrieved from Atlas memory)"]

    courses = {c["id"]: c for c in ctx.get("courses", [])}

    def course_name(cid: str | None) -> str:
        c = courses.get(cid or "")
        return c["name"] if c else "—"

    if ctx.get("learned_facts"):
        lines.append("\n## What you've learned about this student")
        for f in ctx["learned_facts"]:
            label = f" ({f['category']})" if f.get("category") else ""
            lines.append(f"- {f['value']}{label}")

    if ctx.get("courses"):
        lines.append("\n## Courses")
        level_labels = {
            "ap": " AP", "dual_enrollment": " Dual Enrollment", "ib": " IB", "honors": " Honors",
        }
        for c in ctx["courses"]:
            grade = f"{c['current_grade']}% ({c['current_letter']})" if c.get("current_grade") else "no grade yet"
            flags = level_labels.get(c.get("course_level"), "")
            if c.get("has_hn_prep_lab"):
                flags += " +HN Prep Lab"
            if c.get("has_ap_prep_lab"):
                flags += " +AP Prep Lab"
            lines.append(f"- {c['name']}{flags}: {grade}")

    if ctx.get("upcoming"):
        lines.append("\n## Upcoming assignments")
        for a in ctx["upcoming"]:
            lines.append(
                f"- [{a.get('due_date','?')}] {a['title']} ({a['category']}, {a['status']}) "
                f"in {course_name(a.get('course_id'))}"
                + (f", ~{a['estimated_minutes']}min" if a.get("estimated_minutes") else "")
            )

    if ctx.get("upcoming_events"):
        lines.append("\n## Upcoming calendar events (exams, classes, due dates)")
        for e in ctx["upcoming_events"]:
            when = (e.get("starts_at") or "?")[:10] if e.get("all_day") else e.get("starts_at", "?")
            lines.append(
                f"- [{when}] {e['title']} ({e.get('kind','event')}) in {course_name(e.get('course_id'))}"
            )

    if ctx.get("overdue"):
        lines.append("\n## Overdue / missing")
        for a in ctx["overdue"]:
            lines.append(f"- {a['title']} ({a['status']}) in {course_name(a.get('course_id'))}")

    if ctx.get("recent_grades"):
        lines.append("\n## Recent grades")
        for g in ctx["recent_grades"]:
            pct = f"{g['percentage']}%" if g.get("percentage") is not None else "?"
            note = f' — "{g["teacher_comment"]}"' if g.get("teacher_comment") else ""
            lines.append(f"- {course_name(g.get('course_id'))}: {pct}{note}")

    if ctx.get("review_due"):
        lines.append("\n## Concepts due for review (weak retention)")
        for r in ctx["review_due"]:
            lines.append(
                f"- {r.get('name','?')}: mastery {r.get('mastery')}, retention {r.get('retention')}"
            )

    if ctx.get("repeated_mistakes"):
        lines.append("\n## Unresolved mistakes / patterns")
        for m in ctx["repeated_mistakes"]:
            lines.append(f"- ({m.get('mistake_type','?')}) {m['description']}")

    if ctx.get("documents"):
        lines.append("\n## Documents you have on file (search these before answering from general knowledge)")
        for d in ctx["documents"]:
            desc = d.get("summary") or d.get("doc_type") or ""
            lines.append(f"- {d['title']}" + (f" — {desc}" if desc else ""))

    if ctx.get("relevant_passages"):
        lines.append("\n## Relevant passages from your documents")
        for p in ctx["relevant_passages"]:
            snippet = (p.get("content") or "")[:400].replace("\n", " ")
            lines.append(f"- [{p.get('document_title','doc')}] {snippet}")

    if ctx.get("web_results"):
        lines.append(
            "\n## Found online (NOT from the student's documents or records — "
            "label it as web-sourced if you use it)"
        )
        for r in ctx["web_results"]:
            snippet = (r.get("content") or "")[:400].replace("\n", " ")
            lines.append(f"- [{r.get('title','web result')}]({r.get('url','')}) {snippet}")

    return "\n".join(lines)
