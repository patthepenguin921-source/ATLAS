"""Router assembly: generic CRUD + specialized intelligence endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.crud import make_crud_router
from app.routers import (agents, analytics, courses, dashboard, documents,
                         knowledge, reviews, search)

# ---- Generic CRUD resources (writable-field whitelists) ----
_CRUD = [
    make_crud_router(table="terms", prefix="/terms", tag="terms",
                     writable={"name", "start_date", "end_date", "is_current"}),
    make_crud_router(table="teachers", prefix="/teachers", tag="teachers",
                     writable={"name", "email", "subject", "grading_notes", "tendencies"}),
    make_crud_router(table="courses", prefix="/courses", tag="courses",
                     writable={"term_id", "teacher_id", "name", "code", "subject",
                               "course_level", "has_hn_prep_lab", "has_ap_prep_lab",
                               "credit_hours", "color", "period", "room", "sort_order",
                               "semester", "linked_course_id",
                               "external_id", "external_source", "metadata"},
                     default_order="sort_order.asc,created_at.desc"),
    make_crud_router(table="assignments", prefix="/assignments", tag="assignments",
                     writable={"course_id", "term_id", "title", "description", "notes",
                               "category", "status", "assigned_date", "due_date", "submitted_at",
                               "points_possible", "weight", "difficulty", "estimated_minutes",
                               "actual_minutes", "learning_objectives", "tags", "metadata"},
                     default_order="due_date.asc.nullslast"),
    make_crud_router(table="grades", prefix="/grades", tag="grades",
                     writable={"course_id", "assignment_id", "score", "points_possible",
                               "letter", "weight", "graded_at", "teacher_comment", "rubric"}),
    make_crud_router(table="calendar_events", prefix="/calendar", tag="calendar",
                     writable={"course_id", "assignment_id", "title", "description", "location",
                               "starts_at", "ends_at", "all_day", "kind"},
                     default_order="starts_at.asc"),
    make_crud_router(table="study_sessions", prefix="/study-sessions", tag="study",
                     writable={"course_id", "assignment_id", "started_at", "ended_at",
                               "duration_minutes", "focus_rating", "technique", "notes",
                               "concept_ids"},
                     default_order="started_at.desc"),
    make_crud_router(table="announcements", prefix="/announcements", tag="announcements",
                     writable={"course_id", "teacher_id", "title", "body", "posted_at"},
                     default_order="posted_at.desc.nullslast"),
    make_crud_router(table="mistakes", prefix="/mistakes", tag="mistakes",
                     writable={"course_id", "assignment_id", "concept_id", "description",
                               "mistake_type", "correction", "resolved"},
                     default_order="occurred_at.desc"),
    make_crud_router(table="reminders", prefix="/reminders", tag="reminders",
                     writable={"assignment_id", "title", "body", "remind_at", "sent"},
                     default_order="remind_at.asc"),
    make_crud_router(table="chat_projects", prefix="/chat-projects", tag="chat_projects",
                     writable={"name", "color"},
                     default_order="created_at.asc"),
    # Clubs/activities (DECA, etc.) — tracked separately from academic
    # courses; see app.integrations.course_mapping.is_club.
    make_crud_router(table="clubs", prefix="/clubs", tag="clubs",
                     writable={"name", "advisor", "meeting_info",
                               "external_id", "external_source", "metadata"},
                     default_order="name.asc"),
]

api_router = APIRouter()
for r in _CRUD:
    api_router.include_router(r)

api_router.include_router(courses.router)
api_router.include_router(agents.router)
api_router.include_router(documents.router)
api_router.include_router(search.router)
api_router.include_router(dashboard.router)
api_router.include_router(knowledge.router)
api_router.include_router(analytics.router)
api_router.include_router(reviews.router)
