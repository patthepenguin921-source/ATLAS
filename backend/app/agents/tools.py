"""Action tools -- lets the chat agent actually do things in the app
("add this as an assignment", "resync that document", "delete this")
instead of only ever replying with text. Every tool is a concrete, narrow
action against Atlas's own data, described to the LLM in a strict
JSON-Schema shape (`TOOL_SPECS`, consumed by `app.llm.claude.
agentic_complete`) so the model can't invent an action outside what's
actually wired up here.

Destructive tools (`delete_assignment`/`delete_document`/
`delete_calendar_event`) never delete on the model's own say-so:
`execute_tool_for_chat` -- the only callback the model-facing chat loop is
ever given -- resolves the target and returns a `pending_confirmation`
result without touching the row. The chat endpoint surfaces that as a real
Confirm step in the UI; only `confirm_tool`, called from a dedicated
endpoint once a person actually clicks it, performs the delete. This is
enforced structurally (two different entry points, one of which the model
never gets a handle to), not by asking the model nicely to wait.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.supabase_client import eq, supabase
from app.services.grading import ASSIGNMENT_WEIGHTS

# `app.integrations` (course_mapping's package) and `app.routers.documents`
# both transitively import `app.agents.archivist`, which imports
# `app.agents.base` -- the module that imports *this* one. Importing either
# at module load time here would circle straight back into a still-
# initializing `app.agents.base`, so both are deferred to call time instead
# (see `_resolve_course_id`/`_resync_document`), by which point every module
# involved has already finished loading.

_ASSIGNMENT_CATEGORIES = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation", "other",
)

# Statuses a student would plausibly declare themselves via chat. Deliberately
# excludes 'graded' (only a real grade sync produces that) and 'excused'
# (a teacher's call, not the student's) -- update_assignment_status never
# guesses at either.
_ASSIGNMENT_STATUSES = ("not_started", "in_progress", "submitted", "missing", "late")

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "add_assignment",
        "description": (
            "Create a new assignment/task for the student to track. Use this when "
            "the student asks to add, create, or track something as an assignment "
            "(a homework, quiz, test, project, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "A short, clear title for the assignment."},
                "course_name": {
                    "type": "string",
                    "description": "The class this belongs to, if the student named one "
                                   "(matched fuzzily against their real classes).",
                },
                "category": {
                    "type": "string", "enum": list(_ASSIGNMENT_CATEGORIES),
                    "description": "What kind of assignment this is. Default to 'other' if unclear.",
                },
                "due_date": {"type": "string", "description": "Due date as YYYY-MM-DD, if given."},
                "points_possible": {"type": "number", "description": "Points it's worth, if mentioned."},
                "weight_category": {
                    "type": "string", "enum": list(ASSIGNMENT_WEIGHTS),
                    "description": "How much this counts toward the grade, only if the student "
                                   "says or clearly implies it -- a test/project/exam is usually "
                                   "'major', daily homework/classwork is usually 'minor'. Leave "
                                   "unset rather than guessing.",
                },
                "notes": {"type": "string", "description": "Any extra detail the student gave."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Add something to the student's calendar -- a one-off event, reminder, "
            "or appointment (not a graded assignment; use add_assignment for that)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD the event happens."},
                "start_time": {
                    "type": "string",
                    "description": "HH:MM (24h), only if the student gave a specific time -- "
                                   "otherwise this is an all-day entry.",
                },
                "course_name": {"type": "string", "description": "The class this relates to, if any."},
                "description": {"type": "string"},
            },
            "required": ["title", "date"],
        },
    },
    {
        "name": "update_assignment_status",
        "description": (
            "Update the status of an assignment the student already has tracked. Use "
            "this when they say they've started, finished, turned in, or are missing "
            "something (e.g. \"I just submitted the lab report\", \"haven't started "
            "the essay yet\"). Not for creating a new assignment -- use add_assignment "
            "for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The assignment's title (or a close match)."},
                "course_name": {"type": "string", "description": "Narrows the search if ambiguous."},
                "status": {
                    "type": "string", "enum": list(_ASSIGNMENT_STATUSES),
                    "description": "The new status: 'submitted' for turned in/finished-and-sent, "
                                   "'in_progress' for started but not done, 'missing' only if the "
                                   "student says so themselves, 'not_started' to reset, 'late' if "
                                   "they say it's overdue but they're still doing it.",
                },
            },
            "required": ["title", "status"],
        },
    },
    {
        "name": "update_assignment",
        "description": (
            "Edit an assignment's details -- its description/instructions, personal notes, "
            "difficulty, weight, points possible, or due date. Difficulty and weight are what "
            "drive the assignment's risk level (shown as the risk badge / used by the at-risk "
            "list), so use this to raise or lower an assignment's risk when the student says "
            "it's harder/easier than expected or counts for more/less toward the grade. Not "
            "for changing status (use update_assignment_status) or creating a new assignment "
            "(use add_assignment)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The assignment's title (or a close match)."},
                "course_name": {"type": "string", "description": "Narrows the search if ambiguous."},
                "description": {
                    "type": "string",
                    "description": "Replaces the instructions/details text, if the student gave new/updated details.",
                },
                "notes": {"type": "string", "description": "Replaces the student's own notes, if given."},
                "difficulty": {
                    "type": "integer",
                    "description": "1 (easiest) to 5 (hardest) -- directly raises or lowers this "
                                   "assignment's risk level. Use this when the student says an "
                                   "assignment is harder/easier, or asks to raise/lower its risk.",
                },
                "weight_category": {
                    "type": "string", "enum": list(ASSIGNMENT_WEIGHTS),
                    "description": "How much this counts toward the grade -- also affects risk.",
                },
                "points_possible": {"type": "number", "description": "Points it's worth, if given."},
                "due_date": {"type": "string", "description": "New due date as YYYY-MM-DD, if given."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_document",
        "description": (
            "Edit a document's metadata -- its description/summary, keywords, importance, "
            "document type, title, or which class it's filed under. Use this when the student "
            "asks to change, correct, fix, or add to a document's description or other details "
            "(e.g. \"update the description on that syllabus to say...\", \"mark this doc as "
            "high importance\", \"this is actually for AP Biology, not General\", \"rename that "
            "file to...\")."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_title": {"type": "string", "description": "The document's title (or a close match)."},
                "course_name": {"type": "string", "description": "Narrows the search if more than one could match."},
                "summary": {"type": "string", "description": "Replaces the document's description/summary, if given."},
                "keywords": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Replaces the document's keyword tags, if given.",
                },
                "importance": {
                    "type": "string", "enum": ["low", "normal", "high"],
                    "description": "How much this document matters to study/keep track of.",
                },
                "doc_type": {
                    "type": "string",
                    "enum": ["pdf", "powerpoint", "notes", "announcement", "study_guide", "essay",
                             "practice_problems", "rubric", "personal_note", "email", "image",
                             "glance", "other"],
                    "description": "Re-tags what kind of document this is, if the student corrects it.",
                },
                "new_course_name": {
                    "type": "string",
                    "description": "Move the document to a different class, if the student says it belongs "
                                   "elsewhere (matched fuzzily against their real classes).",
                },
                "new_title": {"type": "string", "description": "Renames the document, if given."},
            },
            "required": ["document_title"],
        },
    },
    {
        "name": "generate_practice_quiz",
        "description": (
            "Generate a practice quiz or exam-style practice test on a topic, grounded "
            "in the student's own course material. Use this when they ask to be quizzed, "
            "tested, or given practice questions (e.g. \"quiz me on photosynthesis\", "
            "\"give me a practice test on bio unit 3\"). Reply with the generated "
            "questions formatted clearly (numbered) as part of your answer -- don't just "
            "say you generated them without showing them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "What to quiz/test on."},
                "course_name": {"type": "string", "description": "The class this relates to, if named."},
                "unit_name": {
                    "type": "string",
                    "description": "A specific unit/folder within that class, if named (e.g. 'unit 3').",
                },
                "mode": {
                    "type": "string", "enum": ["quiz", "test"],
                    "description": "'quiz' for a quick check, 'test' for a longer exam-style practice "
                                   "test. Default 'quiz' unless the student says 'test' or 'exam'.",
                },
                "num_questions": {
                    "type": "integer",
                    "description": "How many questions, if the student says. Defaults to 5 for a "
                                   "quiz, 8 for a test.",
                },
                "source": {
                    "type": "string", "enum": ["materials", "internet"],
                    "description": "'materials' (default) grounds questions in the student's own "
                                   "documents/notes. 'internet' instead pulls from a live web "
                                   "search -- use this only if the student explicitly asks for "
                                   "internet-sourced or general (not from their own notes) practice.",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "generate_flashcards",
        "description": (
            "Generate flashcards (key term + definition pairs) from a document or a whole "
            "unit's documents, saved for spaced-repetition review in the Knowledge tab. "
            "Use this when the student asks to make or gather flashcards or key terms "
            "from a document or unit (e.g. \"make flashcards from this doc\", \"gather "
            "key terms from bio unit 3\")."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_title": {
                    "type": "string", "description": "A specific document's title, if the student named one.",
                },
                "unit_name": {
                    "type": "string", "description": "A unit/folder to gather from, if not one specific document.",
                },
                "course_name": {"type": "string", "description": "The class this relates to, if named."},
                "max_cards": {"type": "integer", "description": "Max flashcards to generate. Defaults to 15."},
            },
            "required": [],
        },
    },
    {
        "name": "pull_google_drive_document",
        "description": (
            "Search the student's connected Google Drive for a document by name/topic and "
            "import it into Atlas, filing it under a class. Use this when the student asks "
            "Atlas to pull, grab, fetch, or import a specific document from their Google "
            "Drive (e.g. \"pull my unit 3 study guide from Drive\", \"grab the lab report I "
            "have in Google Docs\"). If Drive isn't connected yet, tell the student to "
            "connect it from Settings > Integrations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for -- the document's name or a close description of it.",
                },
                "course_name": {
                    "type": "string",
                    "description": "The class to file it under, if the student named one -- otherwise "
                                   "Atlas auto-detects the class from the document's content.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "resync_document",
        "description": (
            "Re-fetch the latest version of a document Atlas already has on file, "
            "from its live source (a linked Google Doc/Slides/Sheet), without "
            "running a full Schoology sync. Use this when the student asks to "
            "resync, refresh, or update a specific document (e.g. an \"at a "
            "glance\" schedule doc a teacher keeps editing)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_title": {
                    "type": "string", "description": "The document's title (or a close match).",
                },
                "course_name": {
                    "type": "string", "description": "Narrows the search if more than one could match.",
                },
            },
            "required": ["document_title"],
        },
    },
    {
        "name": "delete_assignment",
        "description": (
            "Delete an assignment. Destructive -- the student is asked to confirm "
            "before anything is actually removed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The assignment's title (or a close match)."},
                "course_name": {"type": "string", "description": "Narrows the search if ambiguous."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_document",
        "description": (
            "Delete a document. Destructive -- the student is asked to confirm "
            "before anything is actually removed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "course_name": {"type": "string", "description": "Narrows the search if ambiguous."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "Delete a calendar event. Destructive -- the student is asked to "
            "confirm before anything is actually removed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "course_name": {"type": "string", "description": "Narrows the search if ambiguous."},
            },
            "required": ["title"],
        },
    },
]

# tool name -> (table, columns) for the generic delete-target resolver.
# `delete_document` isn't here -- it needs the extra storage-cleanup step
# `_perform_delete_document` does, so it's handled separately below.
_DELETE_TABLES: dict[str, tuple[str, str]] = {
    "delete_assignment": ("assignments", "id,title,course_id,due_date"),
    "delete_calendar_event": ("calendar_events", "id,title,course_id,starts_at"),
}


async def _resolve_course_id(
    user_id: str, course_name: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Fuzzy-match `course_name` against the student's own courses (the same
    name-matching the Schoology/PowerSchool integrations use to avoid
    creating duplicate courses -- see `app.integrations.course_mapping`).
    Returns (course_id, None) on a match, (None, None) when no course name
    was given at all (the action just proceeds unfiled), or
    (None, {error}) when a name *was* given but nothing matches -- never
    guesses at a course the student didn't actually name."""
    if not course_name:
        return None, None
    from app.integrations import course_mapping
    courses = await supabase.select(
        "courses", columns="id,name", filters={"user_id": eq(user_id)},
    ) or []
    for c in courses:
        if course_mapping.names_match(c.get("name") or "", course_name):
            return c["id"], None
    low = course_name.strip().lower()
    for c in courses:
        if low and low in (c.get("name") or "").lower():
            return c["id"], None
    names = ", ".join(c["name"] for c in courses if c.get("name")) or "no classes on file yet"
    return None, {
        "status": "error", "error": "course_not_found",
        "message": f'No class matching "{course_name}" found. Their classes: {names}.',
    }


async def _resolve_folder_id(
    user_id: str, unit_name: str | None, *, course_id: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Fuzzy-match `unit_name` against the student's own folders within the
    given course (or General, when `course_id` is None) -- same contract as
    `_resolve_course_id`: (None, None) when no name was given, (id, None) on
    a match, (None, {error}) when a name was given but nothing matches."""
    if not unit_name:
        return None, None
    filters: dict[str, Any] = {"user_id": eq(user_id)}
    filters["course_id"] = eq(course_id) if course_id else "is.null"
    folders = await supabase.select("folders", columns="id,name", filters=filters) or []
    low = unit_name.strip().lower()
    for f in folders:
        if (f.get("name") or "").strip().lower() == low:
            return f["id"], None
    for f in folders:
        if low in (f.get("name") or "").lower():
            return f["id"], None
    names = ", ".join(f["name"] for f in folders if f.get("name")) or "no units/folders there yet"
    return None, {
        "status": "error", "error": "unit_not_found",
        "message": f'No unit/folder matching "{unit_name}" found there. Existing: {names}.',
    }


async def _find_one(
    table: str, user_id: str, title: str, *, course_id: str | None, columns: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fuzzy title search (+ optional course scoping) for a delete/resync
    target. Returns (row, None) on exactly one match, (None, {error}) on
    zero, or (None, {error listing candidates}) on more than one -- a
    destructive tool must never guess which of several matches is meant."""
    filters: dict[str, Any] = {"user_id": eq(user_id), "title": f"ilike.*{title}*"}
    if course_id:
        filters["course_id"] = eq(course_id)
    rows = await supabase.select(table, columns=columns, filters=filters, limit=6) or []
    if not rows:
        return None, {
            "status": "error", "error": "not_found",
            "message": f'Nothing titled like "{title}" found.',
        }
    if len(rows) > 1:
        return None, {
            "status": "error", "error": "ambiguous",
            "message": f'More than one match for "{title}" -- ask the student which one they mean.',
            "candidates": rows,
        }
    return rows[0], None


async def _add_assignment(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    if not title:
        return {"status": "error", "message": "An assignment needs a title."}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    category = args.get("category") if args.get("category") in _ASSIGNMENT_CATEGORIES else "other"
    payload: dict[str, Any] = {
        "user_id": user_id, "title": title, "category": category, "course_id": course_id,
    }
    if args.get("due_date"):
        payload["due_date"] = args["due_date"]
    if args.get("points_possible") is not None:
        payload["points_possible"] = args["points_possible"]
    weight_category = args.get("weight_category")
    if weight_category in ASSIGNMENT_WEIGHTS:
        payload["weight"] = ASSIGNMENT_WEIGHTS[weight_category]
    if args.get("notes"):
        payload["notes"] = args["notes"]
    created = await supabase.insert("assignments", payload)
    row = created[0] if created else payload
    return {
        "status": "done",
        "assignment": {
            "id": row.get("id"), "title": title, "category": category,
            "due_date": args.get("due_date"), "course_id": course_id,
            "weight_category": weight_category if weight_category in ASSIGNMENT_WEIGHTS else None,
        },
    }


async def _add_calendar_event(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    date = (args.get("date") or "").strip()
    if not title or not date:
        return {"status": "error", "message": "A calendar event needs both a title and a date."}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    start_time = (args.get("start_time") or "").strip()
    all_day = not start_time
    starts_at = f"{date}T{start_time}:00" if start_time else date
    payload: dict[str, Any] = {
        "user_id": user_id, "title": title, "course_id": course_id,
        "starts_at": starts_at, "all_day": all_day, "kind": "event",
    }
    if args.get("description"):
        payload["description"] = args["description"]
    created = await supabase.insert("calendar_events", payload)
    row = created[0] if created else payload
    return {
        "status": "done",
        "event": {"id": row.get("id"), "title": title, "date": date, "course_id": course_id},
    }


async def _update_assignment_status(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    status = args.get("status")
    if not title or status not in _ASSIGNMENT_STATUSES:
        return {"status": "error", "message": "Need both an assignment title and a valid status."}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    row, err = await _find_one(
        "assignments", user_id, title, course_id=course_id, columns="id,title,course_id,status",
    )
    if err:
        return err
    patch: dict[str, Any] = {"status": status}
    if status == "submitted":
        patch["submitted_at"] = datetime.now(timezone.utc).isoformat()
    await supabase.update("assignments", patch, filters={"user_id": eq(user_id), "id": eq(row["id"])})
    return {"status": "done", "message": f'Marked "{row["title"]}" as {status.replace("_", " ")}.'}


async def _update_assignment(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    if not title:
        return {"status": "error", "message": "Which assignment should Atlas update?"}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    row, err = await _find_one(
        "assignments", user_id, title, course_id=course_id, columns="id,title,course_id",
    )
    if err:
        return err

    patch: dict[str, Any] = {}
    if args.get("description") is not None:
        patch["description"] = args["description"]
    if args.get("notes") is not None:
        patch["notes"] = args["notes"]
    if args.get("difficulty") is not None:
        try:
            difficulty = int(args["difficulty"])
        except (TypeError, ValueError):
            difficulty = None
        if difficulty is not None and 1 <= difficulty <= 5:
            patch["difficulty"] = difficulty
    weight_category = args.get("weight_category")
    if weight_category in ASSIGNMENT_WEIGHTS:
        patch["weight"] = ASSIGNMENT_WEIGHTS[weight_category]
    if args.get("points_possible") is not None:
        patch["points_possible"] = args["points_possible"]
    if args.get("due_date"):
        patch["due_date"] = args["due_date"]
    if not patch:
        return {"status": "error", "message": "Nothing to update -- give at least one detail to change."}

    await supabase.update("assignments", patch, filters={"user_id": eq(user_id), "id": eq(row["id"])})
    return {"status": "done", "message": f'Updated "{row["title"]}".'}


async def _pull_google_drive_document(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    if not query:
        return {"status": "error", "message": "What should Atlas search for in your Google Drive?"}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err

    from app.integrations import google_oauth  # deferred: see the module docstring
    access_token = await google_oauth.get_drive_access_token(user_id)
    if not access_token:
        return {
            "status": "error",
            "message": "Google Drive isn't connected yet -- connect it from Settings > Integrations first.",
        }

    from app.integrations import google_files  # deferred: see the module docstring
    try:
        matches = await google_files.search_drive_files(access_token, query, max_results=5)
    except RuntimeError as e:
        return {"status": "error", "message": f"Couldn't search Google Drive: {e}"}
    if not matches:
        return {"status": "error", "message": f'No Google Drive file matching "{query}" found.'}

    best = matches[0]
    from app.routers.documents import import_drive_file_core, process_document_core  # deferred: see the module docstring
    try:
        result = await import_drive_file_core(
            user_id=user_id, file_id=best["id"], access_token=access_token,
            course_id=course_id, name=best.get("name"), mime_type=best.get("mimeType"), enrich=True,
        )
    except RuntimeError as e:
        return {"status": "error", "message": f"Couldn't download that file: {e}"}

    doc_id = result["id"]
    try:
        # Indexed inline (synchronously, within this same live request) so
        # the student hears back that it's actually ready -- not a detached
        # background task; if this fails for some reason the document still
        # exists and the safety-net cron (see app.routers.documents' module
        # docstring) picks it up later.
        await process_document_core(user_id, doc_id)
    except Exception:
        pass

    others = len(matches) - 1
    message = f'Pulled "{best.get("name") or query}" from your Google Drive.'
    if others:
        message += (
            f" ({others} other match{'es' if others != 1 else ''} also found -- "
            "ask again with a more specific name if this wasn't the right one.)"
        )
    return {"status": "done", "message": message, "document": {"id": doc_id, "title": best.get("name")}}


async def _resync_document(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("document_title") or "").strip()
    if not title:
        return {"status": "error", "message": "Which document should Atlas re-fetch?"}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    row, err = await _find_one("documents", user_id, title, course_id=course_id, columns="id,title,course_id")
    if err:
        return err
    from app.routers.documents import resync_document_core
    result = await resync_document_core(user_id, row["id"])
    if not result.get("ok"):
        return {"status": "error", "message": result.get("message")}
    if not result.get("changed"):
        return {"status": "done", "message": f'"{row["title"]}" is already up to date -- nothing changed.'}
    return {"status": "done", "message": f'Re-fetched and reprocessed "{row["title"]}".'}


async def _update_document(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("document_title") or "").strip()
    if not title:
        return {"status": "error", "message": "Which document should Atlas update?"}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    row, err = await _find_one(
        "documents", user_id, title, course_id=course_id, columns="id,title,course_id",
    )
    if err:
        return err

    patch: dict[str, Any] = {}
    if args.get("summary") is not None or args.get("keywords") is not None:
        if args.get("summary") is not None:
            patch["summary"] = args["summary"]
        if args.get("keywords") is not None:
            patch["keywords"] = args["keywords"]
        # Same override-survives-re-enrichment idiom as doc_type/importance
        # below -- see Archivist.enrich's summary_source guard.
        patch["summary_source"] = "manual"
    if args.get("importance") in ("low", "normal", "high"):
        patch["importance"] = args["importance"]
        patch["importance_source"] = "manual"
    if args.get("doc_type"):
        patch["doc_type"] = args["doc_type"]
        patch["doc_type_source"] = "manual"
    if args.get("new_title"):
        patch["title"] = args["new_title"].strip()

    new_course_name = (args.get("new_course_name") or "").strip()
    if new_course_name:
        new_course_id, err = await _resolve_course_id(user_id, new_course_name)
        if err:
            return err
        patch["course_id"] = new_course_id
        patch["needs_review"] = False
    if not patch:
        return {"status": "error", "message": "Nothing to update -- give at least one detail to change."}

    await supabase.update("documents", patch, filters={"user_id": eq(user_id), "id": eq(row["id"])})
    return {"status": "done", "message": f'Updated "{row["title"]}".'}


async def _generate_practice_quiz(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    topic = (args.get("topic") or "").strip()
    if not topic:
        return {"status": "error", "message": "What topic should the practice cover?"}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    folder_id, err = await _resolve_folder_id(user_id, args.get("unit_name"), course_id=course_id)
    if err:
        return err
    mode = args.get("mode") if args.get("mode") in ("quiz", "test") else "quiz"
    source = args.get("source") if args.get("source") in ("materials", "internet") else "materials"
    num_questions = args.get("num_questions") or (8 if mode == "test" else 5)
    try:
        num_questions = max(1, min(int(num_questions), 15))
    except (TypeError, ValueError):
        num_questions = 8 if mode == "test" else 5

    from app.agents.tutor import Tutor  # deferred: see the module docstring
    result = await Tutor().quiz(
        user_id, topic, num_questions, mode=mode, course_id=course_id, folder_id=folder_id, source=source,
    )
    questions = result.get("questions") or []
    if not questions:
        return {"status": "error", "message": "Couldn't generate questions for that -- try a more specific topic."}
    from app.services import practice as practice_service  # deferred: see the module docstring
    await practice_service.save_session(
        user_id, topic=result.get("topic", topic), mode=mode, source=source,
        questions=questions, course_id=course_id, folder_id=folder_id,
    )
    return {"status": "done", "mode": mode, "topic": result.get("topic", topic), "questions": questions}


async def _generate_flashcards_tool(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return err
    document_id = None
    document_title = (args.get("document_title") or "").strip()
    if document_title:
        row, err = await _find_one(
            "documents", user_id, document_title, course_id=course_id, columns="id,title,course_id",
        )
        if err:
            return err
        document_id = row["id"]
    folder_id, err = await _resolve_folder_id(user_id, args.get("unit_name"), course_id=course_id)
    if err:
        return err
    if not document_id and not folder_id and not course_id:
        return {"status": "error", "message": "Which document, unit, or class should Atlas make flashcards from?"}

    from app.services import flashcards as flashcards_service  # deferred: see the module docstring
    try:
        max_cards = int(args.get("max_cards") or 15)
    except (TypeError, ValueError):
        max_cards = 15
    result = await flashcards_service.generate(
        user_id, document_id=document_id, course_id=course_id, folder_id=folder_id, max_cards=max_cards,
    )
    if result.get("status") == "error":
        return result
    return {
        "status": "done", "count": result["count"], "source_label": result["source_label"],
        "message": (
            f'Generated {result["count"]} flashcards from "{result["source_label"]}" -- '
            "find them in the Knowledge tab to review."
        ),
    }


async def _resolve_delete_target(
    table: str, user_id: str, args: dict[str, Any], columns: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Shared by both the propose (fuzzy title, from the model) and confirm
    (exact `id`, embedded by a prior propose call) phases of a delete."""
    if args.get("id"):
        rows = await supabase.select(
            table, columns=columns, filters={"user_id": eq(user_id), "id": eq(args["id"])}, limit=1,
        ) or []
        if not rows:
            return None, {
                "status": "error", "error": "not_found",
                "message": "That item no longer exists (maybe already deleted).",
            }
        return rows[0], None
    title = (args.get("title") or "").strip()
    if not title:
        return None, {"status": "error", "message": "Which one? Give a title."}
    course_id, err = await _resolve_course_id(user_id, args.get("course_name"))
    if err:
        return None, err
    return await _find_one(table, user_id, title, course_id=course_id, columns=columns)


async def _propose_delete(name: str, table: str, user_id: str, args: dict[str, Any], columns: str) -> dict[str, Any]:
    row, err = await _resolve_delete_target(table, user_id, args, columns)
    if err:
        return err
    return {
        "status": "pending_confirmation",
        "description": f'Delete "{row["title"]}"?',
        "action": {"name": name, "arguments": {"id": row["id"]}},
    }


async def _perform_delete(table: str, user_id: str, args: dict[str, Any], columns: str) -> dict[str, Any]:
    row, err = await _resolve_delete_target(table, user_id, args, columns)
    if err:
        return err
    await supabase.delete(table, filters={"user_id": eq(user_id), "id": eq(row["id"])})
    return {"status": "done", "message": f'Deleted "{row["title"]}".'}


async def _propose_delete_document(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    row, err = await _resolve_delete_target("documents", user_id, args, "id,title,course_id,storage_path")
    if err:
        return err
    return {
        "status": "pending_confirmation",
        "description": f'Delete the document "{row["title"]}"?',
        "action": {"name": "delete_document", "arguments": {"id": row["id"]}},
    }


async def _perform_delete_document(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    row, err = await _resolve_delete_target("documents", user_id, args, "id,title,course_id,storage_path")
    if err:
        return err
    await supabase.delete("documents", filters={"user_id": eq(user_id), "id": eq(row["id"])})
    if row.get("storage_path"):
        from app.services import storage_cleanup  # local import: avoids a module load-order edge case
        await storage_cleanup.queue_deletion(row["storage_path"])
    return {"status": "done", "message": f'Deleted the document "{row["title"]}".'}


_NON_DESTRUCTIVE_HANDLERS = {
    "add_assignment": _add_assignment,
    "add_calendar_event": _add_calendar_event,
    "update_assignment_status": _update_assignment_status,
    "update_assignment": _update_assignment,
    "generate_practice_quiz": _generate_practice_quiz,
    "generate_flashcards": _generate_flashcards_tool,
    "pull_google_drive_document": _pull_google_drive_document,
    "resync_document": _resync_document,
    "update_document": _update_document,
}


async def execute_tool_for_chat(user_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The only tool-execution entry point the model-facing chat loop is
    ever given (see `app.agents.base.Agent.respond`). A destructive tool is
    never actually performed here, no matter what the model passes -- only
    proposed. See `confirm_tool` for the only path that can actually
    delete anything."""
    if name == "delete_document":
        return await _propose_delete_document(user_id, arguments)
    if name in _DELETE_TABLES:
        table, columns = _DELETE_TABLES[name]
        return await _propose_delete(name, table, user_id, arguments, columns)
    handler = _NON_DESTRUCTIVE_HANDLERS.get(name)
    if not handler:
        return {"status": "error", "message": f"Unknown action: {name}"}
    return await handler(user_id, arguments)


async def confirm_tool(user_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Actually performs a destructive action a person clicked Confirm on
    (`POST /agents/actions/confirm`). `arguments` here is always what a
    prior `execute_tool_for_chat` call embedded in its own
    `pending_confirmation` response -- a resolved row `id`, never a fuzzy
    title -- so this never has to guess or re-match anything."""
    if name == "delete_document":
        return await _perform_delete_document(user_id, arguments)
    if name in _DELETE_TABLES:
        table, columns = _DELETE_TABLES[name]
        return await _perform_delete(table, user_id, arguments, columns)
    return {"status": "error", "message": "Not a confirmable action."}
