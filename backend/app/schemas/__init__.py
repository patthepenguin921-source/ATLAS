"""Pydantic request/response models for Atlas's API."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---- Agents / chat ----
class ChatRequest(BaseModel):
    message: str
    agent: str = "general"
    conversation_id: Optional[str] = None
    include_semantic: bool = True
    # Set by the assistant embedded on a course page (see FolderPane /
    # useChat's `courseId`) so retrieval + the "currently focused on" note
    # in the rendered context are scoped to that one class instead of the
    # student's whole academic record. None everywhere else (the floating
    # chat, the full Ask Atlas page) -- same global behavior as before.
    course_id: Optional[str] = None
    # Further narrows to one folder within that course (or within
    # "General") -- optional, only meaningful alongside course_id.
    folder_id: Optional[str] = None
    # Text already extracted client-side from a file attached via the chat
    # composer's "use for this conversation only" option (see
    # POST /documents/extract-temp) -- injected straight into this turn's
    # context and never persisted server-side. A file saved via the
    # composer's other option ("Save to Documents") instead goes through the
    # normal POST /documents/upload flow and isn't passed here at all.
    attachment_text: Optional[str] = None
    attachment_filename: Optional[str] = None


class RegenerateRequest(BaseModel):
    """Re-runs Atlas's most recent reply in a conversation in place -- see
    `POST /agents/regenerate`. `agent` is whichever specialist is currently
    selected in the composer (may differ from the one that produced the
    original reply, same as if the student had switched agents and re-sent).
    `course_id`/`folder_id` mirror `ChatRequest`'s -- a regenerate from a
    course-scoped chat (see `CourseAssistant`) must keep that same scoping,
    not silently fall back to the student's whole academic record."""

    conversation_id: str
    agent: str = "general"
    course_id: Optional[str] = None
    folder_id: Optional[str] = None


class ActionConfirmRequest(BaseModel):
    """Confirms a destructive action the chat agent proposed (see
    `app.agents.tools`) -- `name`/`arguments` are exactly what that turn's
    `pending_action` contained, never something the frontend constructs
    itself."""

    name: str
    arguments: dict[str, Any]
    conversation_id: str


class MessageFeedbackRequest(BaseModel):
    """A thumbs up/down left on one of Atlas's own replies (see
    `POST /agents/messages/{id}/feedback`) -- `rating: None` clears a
    previously-set reaction rather than requiring a separate delete."""

    rating: Optional[Literal["up", "down"]] = None
    note: Optional[str] = None


class PlanRequest(BaseModel):
    plan_date: Optional[str] = None
    # None means "use the student's configured weekly availability for this
    # date" (see app.services.schedule) rather than a flat guess -- only
    # ever set explicitly by a caller overriding that for one day (e.g. "I
    # actually only have 20 minutes today").
    available_minutes: Optional[int] = None


class ExplainRequest(BaseModel):
    topic: str
    depth: str = "standard"  # quick | standard | deep
    course_id: Optional[str] = None
    folder_id: Optional[str] = None


class QuizRequest(BaseModel):
    topic: str
    num_questions: int = 5
    mode: str = "quiz"  # quiz | test
    course_id: Optional[str] = None
    folder_id: Optional[str] = None


class AnalyzeRequest(BaseModel):
    question: Optional[str] = None


class ReviewRequest(BaseModel):
    week_start: Optional[str] = None


# ---- Study planning ----
class StudyAvailabilityRequest(BaseModel):
    """Replaces the student's whole weekly study-availability schedule in one
    call -- see `PUT /study-availability`. Keys are 0 (Monday) through 6
    (Sunday); values are minutes available that day. `0` is a legitimate
    "no time that day", not "unset"."""

    schedule: dict[int, int]


# ---- Knowledge model ----
class KnowledgeReviewRequest(BaseModel):
    concept_id: str
    quality: int = Field(ge=0, le=5)
    confidence: Optional[float] = None


# ---- Flashcards ----
class FlashcardGenerateRequest(BaseModel):
    """Exactly one scope: a single document, a unit/folder, or a whole
    class -- see `POST /flashcards/generate`."""

    document_id: Optional[str] = None
    course_id: Optional[str] = None
    folder_id: Optional[str] = None
    max_cards: int = 15


class FlashcardReviewRequest(BaseModel):
    quality: int = Field(ge=0, le=5)


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


_DOC_TYPES = Literal[
    "pdf", "powerpoint", "notes", "announcement", "study_guide", "essay",
    "practice_problems", "rubric", "personal_note", "email", "image", "glance", "other",
]


class DocumentPatchRequest(BaseModel):
    """Used by the bulk-upload review screen to correct an auto-detected
    course, and by the documents page to re-title a document, re-tag its
    type, move it to a different folder, or override its importance
    rating."""

    course_id: Optional[str] = None
    title: Optional[str] = None
    needs_review: Optional[bool] = None
    importance: Optional[Literal["low", "normal", "high"]] = None
    doc_type: Optional[_DOC_TYPES] = None
    # Moving a document into (or out of, via null) a subfolder. Setting this
    # always marks `folder_source: manual` server-side (see
    # POST /documents/{id}) so the Archivist's auto-sort never re-files a
    # document a student has already moved themselves.
    folder_id: Optional[str] = None


# ---- Folders ----
class FolderCreateRequest(BaseModel):
    """A new folder/divider — either at a class's top level (``course_id``
    set, no ``parent_folder_id``), inside "General" (neither set), or as a
    subfolder of an existing folder (``parent_folder_id`` set — its scope,
    course or General, is inherited from that parent; any ``course_id``
    passed alongside a parent is ignored)."""

    name: str
    course_id: Optional[str] = None
    parent_folder_id: Optional[str] = None


class FolderPatchRequest(BaseModel):
    """Rename, reorder, or move a folder to a different parent within the
    same class/General scope."""

    name: Optional[str] = None
    parent_folder_id: Optional[str] = None
    sort_order: Optional[int] = None


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


# ---- Integrations ----
class PowerSchoolConnectRequest(BaseModel):
    base_url: str          # e.g. https://<district>.powerschool.com
    username: str
    password: str
    display_name: Optional[str] = None


class PowerSchoolConnectSessionRequest(BaseModel):
    """For districts using SSO (Google/Microsoft/Clever) — no username/password
    form exists to automate, so the caller pastes a session cookie from their
    own already-authenticated browser instead."""

    base_url: str
    cookie: str
    display_name: Optional[str] = None


class SchoologyConnectRequest(BaseModel):
    """Schoology connect: username + password — the same login used in a
    browser — reads courses and materials the same way the student's own
    authenticated web session does. A personal consumer key + secret
    (generated at ``<domain>/api``) is optional; when supplied it
    additionally unlocks assignments/events sync, which the login session
    alone can't read yet (no scraper for those, only for materials — see
    ``schoology_scraper.py``). The REST host is always api.schoology.com/v1;
    ``api_base`` only overrides it if ever needed."""

    domain: str                             # e.g. https://yourdistrict.schoology.com
    username: str
    password: str
    consumer_key: Optional[str] = None
    consumer_secret: Optional[str] = None
    api_base: Optional[str] = None
    display_name: Optional[str] = None


# ---- Generic ----
class GenericBody(BaseModel):
    """Free-form body for CRUD create/update; fields validated per-table."""

    model_config = {"extra": "allow"}

    def data(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)
