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

from typing import Any

from app.core.supabase_client import eq, supabase

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
    if args.get("notes"):
        payload["notes"] = args["notes"]
    created = await supabase.insert("assignments", payload)
    row = created[0] if created else payload
    return {
        "status": "done",
        "assignment": {
            "id": row.get("id"), "title": title, "category": category,
            "due_date": args.get("due_date"), "course_id": course_id,
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
    "resync_document": _resync_document,
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
