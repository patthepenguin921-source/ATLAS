"""Detecting and extracting a class schedule from an "at a glance" document.

A teacher's "Week at a Glance"/"Unit at a Glance" document lays out what
happens in class on specific days -- topics, activities, and often
assignments due -- in whatever shape the teacher wrote it (a table, a
bulleted list, prose). Matched first by title/filename, e.g. "Week at a
Glance", "Unit 4 - At a Glance.pdf", "Chapter 7 – AT A GLANCE" -- any of
these should be treated as a schedule document and mined for a day-by-day
`calendar_events` rundown (`kind="class"`) plus any assignments it mentions.

A title match isn't the only signal, though: an LMS item is often named for
*what it contains* rather than by the "at a glance" phrasing its own
document uses -- confirmed against a real account, a Schoology item titled
"AP Calculus BC Unit 1 Assignments List" (nested under a folder path that
happened to read "Unit at Glance", which callers never even see -- only the
item's own name is title-matched) turned out to open with the heading "AP
Calculus BC Unit 1 **at a Glance**". `is_glance`/`is_recurring_glance` fall
back to sniffing the opening of the document's own extracted text once
there's actually text to check (title-only detection, via `is_glance_title`/
`is_recurring_glance_title`, is all that's available before a file is
downloaded/extracted, or when only a stored title is on hand -- e.g. the
materials-sync dedupe set, which never re-fetches an already-known item's
content just to re-classify it).

Shared by the Schoology materials-sync path (`app.integrations.schoology`)
and the generic upload pipeline (`app.routers.documents`) so a document
matching this pattern gets the same treatment regardless of how it arrived.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
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
# spaces were replaced with hyphens. The "a" itself is optional -- a real
# Schoology folder was found named "Unit at Glance" (no "a") -- so
# `[\s-]+(?:a[\s-]+)?` still requires "at" directly leading into "glance"
# (with or without the "a") rather than loosening the match generally.
GLANCE_TITLE_RE = re.compile(r"at[\s-]+(?:a[\s-]+)?glance", re.I)

# How much of a document's own extracted text is worth checking for the
# glance phrase when its title/filename didn't already say so -- just the
# opening (a heading is always near the top of one of these), not a full-
# document scan, so a document that merely mentions "at a glance" somewhere
# deep in unrelated prose isn't misclassified as a schedule document.
_CONTENT_SNIFF_CHARS = 300

# A glance document scoped to something longer than a single week -- a unit,
# semester, quarter, chapter, term, etc. -- is something a teacher keeps
# editing as the unit/term actually progresses (new days/assignments added
# as they're taught), unlike a "Week at a Glance"/"Day at a Glance" which is
# finished once its dates pass and never touched again. `is_recurring_glance_
# title` marks these so the sync can keep re-checking them on every run
# instead of treating a first pull as done forever.
_BROAD_SCOPE_RE = re.compile(r"\b(unit|semester|quarter|trimester|term|chapter|month|course|year)s?\b", re.I)

_CATEGORY_KEYWORDS = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation",
)
_ASSIGNMENT_CATEGORY_VALUES = frozenset(_CATEGORY_KEYWORDS) | {"other"}


def is_glance_title(title: str | None) -> bool:
    """True if a document's title/filename marks it as an "at a glance"
    schedule document."""
    return bool(title and GLANCE_TITLE_RE.search(title))


def is_recurring_glance_title(title: str | None) -> bool:
    """True for an "at a glance" document scoped to more than a single week
    (a "Unit at a Glance", "Semester at a Glance", ...) -- see the module-
    level comment on `_BROAD_SCOPE_RE`."""
    return is_glance_title(title) and bool(_BROAD_SCOPE_RE.search(title or ""))


def is_glance(*, title: str | None = None, text: str | None = None) -> bool:
    """True if this document is an "at a glance" schedule document, by its
    title/filename or (as a fallback, once there's text to check) its own
    opening content -- see the module docstring for why the fallback
    exists. `text` is optional so this still works as a pure title check
    when no content is available yet (matching `is_glance_title`)."""
    if is_glance_title(title):
        return True
    return bool(text) and bool(GLANCE_TITLE_RE.search(text[:_CONTENT_SNIFF_CHARS]))


def is_recurring_glance(*, title: str | None = None, text: str | None = None) -> bool:
    """`is_glance`, further scoped to more than a single week -- see
    `is_recurring_glance_title`. Checks the same title-or-content
    combination for the scope keyword (unit/semester/quarter/...)."""
    if not is_glance(title=title, text=text):
        return False
    if is_recurring_glance_title(title):
        return True
    return bool(text) and bool(_BROAD_SCOPE_RE.search(text[:_CONTENT_SNIFF_CHARS]))


# Grace window (past a glance document's own last-extracted date range)
# before Atlas stops bothering to re-check it -- covers a sync landing a
# day or two late, not meant to be generous.
_GLANCE_RELEVANCE_GRACE_DAYS = 5


def glance_still_relevant(metadata: dict[str, Any] | None) -> bool:
    """True while today is still within (or shortly after) the date range a
    glance document's own last successful extraction covered, or when
    there's no recorded range yet to judge by (nothing pulled yet, or a row
    that predates this field existing) -- keep trying in that case rather
    than assume it's stale. Once every date the document mentions is safely
    in the past, there's nothing left in it worth re-mining even if the file
    itself gets edited later (e.g. a typo fix to an old week's page)."""
    date_range = (metadata or {}).get("glance_date_range")
    if not isinstance(date_range, dict) or not date_range.get("end"):
        return True
    try:
        end = date.fromisoformat(date_range["end"])
    except (TypeError, ValueError):
        return True
    return date.today() <= end + timedelta(days=_GLANCE_RELEVANCE_GRACE_DAYS)


def _map_category(text: str) -> str:
    low = (text or "").lower()
    for key in _CATEGORY_KEYWORDS:
        if key in low:
            return key
    return "other"


def _normalize_name(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def valid_iso_date(value: Any) -> str | None:
    """An LLM asked for a due date as "YYYY-MM-DD or null" doesn't always
    comply -- a real response once returned a bare weekday name ("Monday"),
    lifted straight from the document's own "to be finished Monday" phrasing,
    instead of a resolved calendar date. Passed straight through as
    `due_date`, that isn't just a wrong date -- Postgres rejects the whole
    assignment insert outright (`invalid input syntax for type timestamp
    with time zone`), losing the assignment entirely rather than just its
    due date. Shared by every LLM-derived due_date in this module and
    `app.integrations.schoology` so each has one place to fall back to a
    real date (or null) instead of a garbage string reaching the database."""
    if not isinstance(value, str):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


async def extract_schedule_from_text(
    title: str, text: str, *, report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ask the reasoning engine to read an 'at a glance' document like a
    student would and return one entry per day it actually names, each with
    the day's topic and any assignment due. No LLM configured means no
    schedule -- free-form day/date layouts have no reliable regex/DOM to
    parse, so there's no safe non-LLM fallback here.

    A genuine LLM/parse failure is reported (into `report["errors"]` when a
    report is on hand) rather than swallowed into the same empty list a
    document with no real schedule content would also produce -- those two
    cases used to be indistinguishable, which made a transient failure look
    exactly like "nothing here" instead of a retriable problem. Still never
    raises: one document's extraction failing must not abort the rest of a
    sync, same as every other best-effort step in this pipeline."""
    if not settings.has_llm or not text.strip():
        return []
    excerpt = text.strip()[:12000]
    today = date.today().isoformat()
    prompt = f"""\
A document titled "{title}" lays out what happens in class on specific days \
(a "week at a glance" or "unit at a glance" schedule). Today's date is \
{today}.

Read the document and list every day it actually names, with a short topic \
summary and any real assignment due that day.

DATES:
- If the document states an explicit year anywhere (a header like "8/4 \
through 8/21/2026", "Fall 2026", etc.), use that year for every date in the \
document that doesn't state its own year -- don't fall back to guessing \
from today's date once the document has already told you the year.
- Only when no year is stated anywhere in the document, resolve a missing \
year to whichever nearby year (today's, or the one before/after) puts the \
date closest to today -- don't default to today's year if that would place \
the date many months away in either direction while a different year would \
not.
- If a day is also labeled with its day of the week (e.g. "Monday 10/6"), \
the date you resolve must actually fall on that weekday of the calendar -- \
if no date satisfies both the stated weekday and the stated month/day, omit \
that entry rather than guessing.
- Only report a date or assignment the document actually states -- never \
infer or invent one that isn't really there.

WHAT COUNTS AS AN ASSIGNMENT -- be selective; most of these documents \
describe far more class activity than real, trackable assignments:
- A test, quiz, exam, project, essay, or anything else graded or turned in \
is always a real assignment -- capture it wherever it appears in the \
document, even if it's only mentioned in a day's class-agenda/notes rather \
than a separate "assignments"/"homework" column. Missing a test or quiz \
matters far more than missing a minor practice item.
- A specific, named piece of homework the student is expected to complete \
(a worksheet, a problem set, a reading, a written response) is a real \
assignment even if the document files it under a generic column header like \
"Suggested Assignments" -- that header doesn't mean optional, it's just \
this document's own label for its homework column.
- Do NOT create a separate assignment for every individual in-class \
activity, video, or practice-question set a day's agenda lists (e.g. "Watch \
video 3.2", "Practice questions 1-19", "Delta Math practice") -- these \
describe what happens in class, not a discrete thing to track; fold them \
into that day's topic summary instead of listing each as an assignment.
- A reference to a specific, numbered set of items on a named classroom \
platform (e.g. "CK 2 - 4", "Classkick 5-7", "IXL A.2") IS a discrete thing \
to track, unlike a vague "practice" bullet -- a number or range attached to \
a platform name means specific items the student must go complete, not just \
in-class activity. Capture one assignment per such listing (don't split a \
range like "CK 2 - 4" into three separate assignments).
- Teachers often abbreviate a platform/tool name instead of writing it out \
(e.g. "CK" for Classkick). When you're confident what a short code stands \
for, spell out the full name in the assignment title so it's clear what it \
refers to (e.g. "CK 2 - 4" -> "Classkick 2 - 4") -- but if you don't \
recognize a code, leave it exactly as written rather than guessing.
- Skip anything the document itself marks as optional, not graded, \
practice-only, or "as needed" (e.g. "(practice -- not graded)", "Optional: \
..."). These aren't real due work even if they're worth mentioning in the \
day's topic.
- Skip non-academic asides that aren't actual work at all (a holiday/break \
greeting, "enjoy your weekend", a reminder to bring materials).

DUE DATES:
- If the document states a rule for when things are due (e.g. "assignments \
are due the school day immediately following the assigned date"), apply \
it -- the due_date is not always the same day the item is mentioned; work \
out which school day (skip weekends) that rule actually points to.
- Otherwise, an assignment's due date is the day it's listed under, unless \
the document gives it an explicit different due date.

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
            # A real "Unit ... Assignments List" document (see this module's
            # docstring) genuinely produced enough days/assignments that
            # 4000 tokens cut the JSON off mid-array -- an invalid, truncated
            # response `complete_json`'s own fence-stripping/brace-matching
            # fallback can't repair (`json.JSONDecodeError: Expecting value`),
            # losing the *entire* extraction rather than just the tail end of
            # a long document. 8000 comfortably fits a full unit/semester's
            # worth of days without materially increasing single-document
            # cost for the common (much shorter) case.
            prompt=prompt, max_tokens=8000, temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        if report is not None:
            report["errors"].append(f"{title}: couldn't extract a schedule from this document ({e})")
        return []
    days = result.get("days") if isinstance(result, dict) else None
    if not isinstance(days, list):
        if report is not None:
            report["errors"].append(f"{title}: schedule extraction returned an unexpected shape")
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
    """`fields` deliberately never includes `status` -- only a brand-new row
    gets seeded as "not_started"; an update leaves whatever status is
    already there untouched (a PATCH only ever changes the columns given).
    Without this, re-mining a recurring glance doc after an edit anywhere
    in it would reset every assignment it lists back to "not started" on
    every sync, wiping a student's actual progress."""
    existing = await supabase.select(
        "assignments", columns="id",
        filters={"user_id": eq(user_id), "external_id": eq(external_id),
                 "external_source": eq(source)}, limit=1,
    )
    payload = {**fields, "user_id": user_id, "external_id": external_id, "external_source": source}
    if existing:
        await supabase.update("assignments", payload, filters={"id": eq(existing[0]["id"])})
    else:
        await supabase.insert("assignments", {**payload, "status": "not_started"})


async def _reconcile_glance_rows(
    *, user_id: str, course_id: str, source: str, source_document_id: str,
    touched_assignment_ids: set[str], touched_event_ids: set[str],
    report: dict[str, Any] | None,
) -> None:
    """Delete any assignment/calendar_event this exact document previously
    created that this run's fresh extraction no longer lists -- a day or
    assignment dropped from a re-edited glance doc should disappear from
    Atlas too, not linger forever. Scoped strictly to rows this document
    produced (`metadata.source_document_id`), so this can never touch a row
    any other document, sync, or manual entry created -- and only ever
    called when this run's extraction actually found at least one day (see
    `apply_schedule_from_doc`), so a transient LLM failure can never look
    like "the document now has nothing in it" and wipe everything out."""
    try:
        existing_assignments = await supabase.select(
            "assignments", columns="id,external_id",
            filters={"user_id": eq(user_id), "course_id": eq(course_id),
                     "external_source": eq(source),
                     "metadata->>source_document_id": eq(source_document_id)},
        ) or []
        for row in existing_assignments:
            if row.get("external_id") not in touched_assignment_ids:
                await supabase.delete("assignments", filters={"id": eq(row["id"])})
    except Exception as e:  # noqa: BLE001
        if report is not None:
            report["errors"].append(f"couldn't clean up assignments no longer on the schedule: {e}")
    try:
        existing_events = await supabase.select(
            "calendar_events", columns="id,external_id",
            filters={"user_id": eq(user_id), "course_id": eq(course_id),
                     "metadata->>source_document_id": eq(source_document_id)},
        ) or []
        for row in existing_events:
            if row.get("external_id") not in touched_event_ids:
                await supabase.delete("calendar_events", filters={"id": eq(row["id"])})
    except Exception as e:  # noqa: BLE001
        if report is not None:
            report["errors"].append(f"couldn't clean up class-schedule days no longer on the schedule: {e}")


async def _purge_orphaned_glance_rows(
    *, user_id: str, course_id: str, source: str, report: dict[str, Any] | None,
) -> None:
    """Delete glance-derived assignments/calendar_events left behind by a
    document that no longer exists at all -- e.g. one replaced under a
    fresh id after a duplicate-ingest race, or removed by a manual cleanup
    that only caught some of what it created. `_reconcile_glance_rows`
    above only ever looks at rows tied to *this* run's own document id, so
    it can never see (let alone clean up) a row still pointing at some
    other, now-deleted document -- confirmed against a real account where
    exactly this left a "Unit at a Glance" course showing two conflicting
    sets of class days for the same two weeks, one set from a document
    that had already been deleted. Scoped to this course/source so it can
    never touch another course's or another sync source's rows, and only
    ever deletes a row once its own referenced document is confirmed gone
    -- a row whose document still exists is never touched here, no matter
    what this run's own extraction found."""
    for table, extra_filters in (
        ("assignments", {"external_source": eq(source)}),
        ("calendar_events", {}),
    ):
        try:
            rows = await supabase.select(
                table, columns="id,metadata",
                filters={"user_id": eq(user_id), "course_id": eq(course_id),
                         "metadata->>detected_from": eq("glance_doc"), **extra_filters},
            ) or []
            doc_ids = {
                (row.get("metadata") or {}).get("source_document_id") for row in rows
            }
            doc_ids.discard(None)
            if not doc_ids:
                continue
            existing = await supabase.select(
                "documents", columns="id", filters={"id": f"in.({','.join(doc_ids)})"},
            ) or []
            existing_ids = {d["id"] for d in existing}
            for row in rows:
                sdid = (row.get("metadata") or {}).get("source_document_id")
                if sdid and sdid not in existing_ids:
                    await supabase.delete(table, filters={"id": eq(row["id"])})
        except Exception as e:  # noqa: BLE001
            if report is not None:
                report["errors"].append(f"couldn't clean up orphaned {table} rows: {e}")


async def _tag_as_glance(document_id: str) -> None:
    """Force a schedule ("at a glance") document's own `doc_type` to
    `glance` instead of whatever the general Archivist enrichment pass
    separately guessed from the content alone (commonly `study_guide` --
    a reasonable guess, but wrong: by the time this runs, Atlas already
    knows for certain this is a schedule document, so it shouldn't need to
    guess at all). Never overwrites a student's own manual re-tag from the
    documents page -- `doc_type_source` mirrors `importance_source`
    (see `app.routers.documents.update_document`): only ever set
    automatically here when it's still AI/system-sourced or unset."""
    try:
        rows = await supabase.select(
            "documents", columns="doc_type_source",
            filters={"id": eq(document_id)}, limit=1,
        )
        if rows and rows[0].get("doc_type_source") == "manual":
            return
        await supabase.update(
            "documents", {"doc_type": "glance", "doc_type_source": "system"},
            filters={"id": eq(document_id)},
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort, same as the rest of this pipeline


async def _record_glance_date_range(source_document_id: str, dates: list[str]) -> None:
    """Persist the span of dates this document's latest successful
    extraction actually covered, so `glance_still_relevant` can tell once
    every date in it is safely in the past. Merges into whatever metadata
    is already on the row (content_hash, is_glance, ...) rather than
    overwriting it."""
    if not dates:
        return
    try:
        rows = await supabase.select(
            "documents", columns="metadata", filters={"id": eq(source_document_id)}, limit=1,
        )
        if not rows:
            return
        metadata = dict(rows[0].get("metadata") or {})
        metadata["glance_date_range"] = {"start": min(dates), "end": max(dates)}
        await supabase.update("documents", {"metadata": metadata}, filters={"id": eq(source_document_id)})
    except Exception:  # noqa: BLE001
        pass  # best-effort -- worst case, the doc just keeps getting rechecked


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
    other's rows for the same course/date.

    An assignment a glance doc mentions is often *also* already on file for
    this course -- imported for real via the LMS's own assignments API/scrape
    (see `SchoologyProvider._import_assignment`/`_ingest_scraped_assignment`)
    -- since a teacher's schedule doc routinely just restates a due date
    that's already tracked elsewhere. Dedupe by normalized title against
    every *non-glance* assignment already on file for this course before
    inserting a new one, the same way `_ingest_scraped_assignment` does, so
    a title match against a genuinely independent import counts and it's
    pulled once, not twice. Every glance-derived row (from this document or
    any other) is excluded from that check entirely, not just this
    document's own -- a coincidental title match between two different
    glance documents (two different weeks both mentioning a "Quiz", say)
    must never suppress a real item; each document's own rows are kept
    distinct by `external_id` (which embeds `source_document_id`), so a
    re-sync still updates this document's own previously-created row in
    place via `_upsert_assignment`'s upsert-by-external_id instead of
    either duplicating or falsely colliding with it.

    Everything this call writes (or previously wrote, on an earlier run for
    this same document) is reconciled against what this run's extraction
    actually found: unmentioned assignments/days from a prior run are
    deleted, changed ones (e.g. a moved due date) are updated in place, and
    new ones are created -- see `_reconcile_glance_rows`. Skipped entirely
    when this run found nothing at all, so a transient LLM failure can
    never be mistaken for "the document now has zero days" and wipe out
    everything a previous, successful run saved."""
    if source_document_id:
        await _tag_as_glance(source_document_id)
    days = await extract_schedule_from_text(title, text, report=report)
    existing_assignments = await supabase.select(
        "assignments", columns="external_id,title",
        filters={"user_id": eq(user_id), "course_id": eq(course_id)},
    ) or []
    other_assignment_titles = {
        _normalize_name(row.get("title") or "")
        for row in existing_assignments
        if ":glance-assignment:" not in str(row.get("external_id") or "")
    } - {""}

    touched_assignment_ids: set[str] = set()
    touched_event_ids: set[str] = set()
    dates: list[str] = []

    for day in days:
        iso_date = day.get("date")
        if not iso_date:
            continue
        dates.append(iso_date)
        event_ext_id = f"{source}:class:{course_id}:{source_document_id}:{iso_date}"
        touched_event_ids.add(event_ext_id)
        try:
            await _upsert_calendar_event(
                user_id, event_ext_id, {
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
            if _normalize_name(a_title) in other_assignment_titles:
                if report is not None:
                    report["skipped"] = report.get("skipped", 0) + 1
                continue
            category = a.get("category")
            if category not in _ASSIGNMENT_CATEGORY_VALUES:
                category = _map_category(a_title)
            due = valid_iso_date(a.get("due_date")) or iso_date
            # Keyed by the day it's *listed* under (`iso_date`), not its
            # due_date -- so the same title mentioned on two different days
            # in the same document (a recurring "Study for Test" reminder,
            # a weekly "Quiz") stays two distinct rows instead of the
            # second silently overwriting the first, while a resync that
            # only moves *this same listing's* due date still updates the
            # same row in place rather than duplicating it.
            ext_id = (
                f"{source}:glance-assignment:{course_id}:{source_document_id}:"
                f"{iso_date}:{_normalize_name(a_title)}"
            )
            touched_assignment_ids.add(ext_id)
            try:
                await _upsert_assignment(user_id, ext_id, source, {
                    "course_id": course_id, "title": a_title, "category": category,
                    "due_date": due,
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

    if days and source_document_id:
        await _reconcile_glance_rows(
            user_id=user_id, course_id=course_id, source=source,
            source_document_id=source_document_id,
            touched_assignment_ids=touched_assignment_ids, touched_event_ids=touched_event_ids,
            report=report,
        )
        await _purge_orphaned_glance_rows(
            user_id=user_id, course_id=course_id, source=source, report=report,
        )
        await _record_glance_date_range(source_document_id, dates)
