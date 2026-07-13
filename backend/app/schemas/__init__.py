"""Pydantic request/response models for Atlas's API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---- Agents / chat ----
class ChatRequest(BaseModel):
    message: str
    agent: str = "general"
    conversation_id: Optional[str] = None
    include_semantic: bool = True


class PlanRequest(BaseModel):
    plan_date: Optional[str] = None
    available_minutes: int = 180


class ExplainRequest(BaseModel):
    topic: str
    depth: str = "standard"  # quick | standard | deep


class QuizRequest(BaseModel):
    topic: str
    num_questions: int = 5


class AnalyzeRequest(BaseModel):
    question: Optional[str] = None


class ReviewRequest(BaseModel):
    week_start: Optional[str] = None


# ---- Knowledge model ----
class KnowledgeReviewRequest(BaseModel):
    concept_id: str
    quality: int = Field(ge=0, le=5)
    confidence: Optional[float] = None


# ---- Search ----
class SearchRequest(BaseModel):
    query: str
    limit: int = 8
    course_id: Optional[str] = None
    threshold: float = 0.0


# ---- Documents ----
class IngestTextRequest(BaseModel):
    title: str
    text: str
    course_id: Optional[str] = None
    doc_type: str = "notes"
    enrich: bool = True


# ---- Courses ----
class SplitSemestersRequest(BaseModel):
    """Split a course into two linked semester rows (S1 / S2)."""

    s1_level: Optional[str] = None   # course_level for semester 1 (defaults to current)
    s2_level: str = "ap"             # course_level for semester 2
    s1_has_hn_prep_lab: bool = True  # HN prep lab weights S1 at 5.5
    s2_has_ap_prep_lab: bool = False


# ---- Drive import ----
class DriveImportRequest(BaseModel):
    file_id: str
    access_token: str
    course_id: str
    name: Optional[str] = None
    mime_type: Optional[str] = None
    enrich: bool = True


# ---- Generic ----
class GenericBody(BaseModel):
    """Free-form body for CRUD create/update; fields validated per-table."""

    model_config = {"extra": "allow"}

    def data(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)
