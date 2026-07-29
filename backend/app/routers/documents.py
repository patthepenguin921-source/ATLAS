"""Documents — upload, ingest (chunk+embed), enrich, and manage files.

Indexing (chunk/embed) and AI enrichment never run inline with the upload
request itself, and never as a FastAPI `BackgroundTask` either — a
background task kept alive past the response getting sent would sometimes
just never finish in production on Cloud Run: Cloud Run only allocates CPU
to a container while it's actively serving a request, so once the response
goes out the process can be frozen mid-task with no guarantee it's ever
scheduled again. (Vercel's serverless runtime carries the same risk.)

Instead, `POST /documents/{id}/process` does the work — but as its own,
separate live request, which gets genuine CPU the same way any request
does. The frontend fires this immediately after an upload/bulk-upload/Drive
import finishes (fire-and-forget — the upload response itself stays fast),
and the same endpoint backs the documents page's "Index now" button for
anything still pending or failed (e.g. the tab closed before the
post-upload call landed). `/documents/cron/process-pending` still exists as
a low-frequency safety net behind it (see automation/README.md) in case
neither of those ever fires for a given document — not the primary path
anymore.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.agents.archivist import Archivist
from app.config import settings
from app.core.r2_client import r2, safe_object_name
from app.core.security import CurrentUser, check_cron_secret, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import DocumentPatchRequest, DriveImportRequest, IngestTextRequest
from app.services import ingestion, storage_cleanup
from app.services.schedule_extraction import apply_schedule_from_doc, is_glance_title

router = APIRouter(prefix="/documents", tags=["documents"])

# Below this, a course auto-detection guess is flagged for the student to
# double-check rather than trusted outright.
_REVIEW_CONFIDENCE_THRESHOLD = 0.6

# How many not-yet-indexed documents the cron claims per invocation, and how
# long a claim is honored before a later run is allowed to retry the same
# document (in case whatever claimed it died mid-processing without ever
# reporting an error) — same idea as app.integrations' STALE_RUNNING_MINUTES.
_PROCESS_BATCH_SIZE = 10
_STALE_CLAIM_MINUTES = 10

# ingestion.extract_text's layout-aware PDF pass (pymupdf4llm) is a plain
# CPU-bound call with no timeout of its own — its running time scales with
# a page's visual complexity, not just its size, and a large/dense PDF (a
# real College Board Course and Exam Description was the case that
# surfaced this — 150+ pages, hit this repeatedly across every processing
# approach tried, not just this one) can make it hang effectively forever.
# Bounding it and falling back to extract_text_fast (pypdf's much faster,
# if less layout-accurate, walk) is what actually stops a claimed document
# from sitting stuck: everything downstream (chunk/embed/enrich) already
# has its own HTTP timeouts, but this step had none.
_TEXT_EXTRACTION_TIMEOUT_SECONDS = 60
_TEXT_EXTRACTION_FAST_TIMEOUT_SECONDS = 30


async def _extract_text_bounded(content: bytes, mime_type: str, filename: str) -> str:
    """`ingestion.extract_text`, but bounded — falls back to
    `ingestion.extract_text_fast` if the layout-aware pass doesn't finish in
    time, and to no text at all if even that doesn't. See the module-level
    comment above for why this exists instead of just calling extract_text
    directly."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(ingestion.extract_text, content, mime_type, filename),
            timeout=_TEXT_EXTRACTION_TIMEOUT_SECONDS,
        )
    except Exception:
        pass
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(ingestion.extract_text_fast, content, mime_type, filename),
            timeout=_TEXT_EXTRACTION_FAST_TIMEOUT_SECONDS,
        )
    except Exception:
        return ""


async def _prepare_and_store(
    *, user_id: str, course_id: str | None, content: bytes, filename: str,
    content_type: str, title: str | None, enrich: bool,
) -> dict:
    """(Convert images→PDF) → store to R2 → classify → record. This is *all*
    that happens before the upload response goes out — the document row is
    created with ``ingested: false`` ("pending" in the UI) and nothing else,
    since indexing/enrichment now always run later via the processing cron
    (see the module docstring for why).

    Used by direct upload, bulk upload, and Google Drive import so they all
    behave identically. ``title`` is optional; when omitted, the Archivist
    generates one from the document's content. ``course_id`` is optional —
    when omitted (bulk upload), the course is auto-detected from the
    student's existing classes and low-confidence guesses are flagged via
    ``needs_review`` instead of being left unfiled. Auto-detection needs the
    document's text, so bulk upload is the one case that still extracts text
    synchronously here (the cron re-extracts it later for indexing — a small
    duplicate cost, not worth a second persisted copy just to skip it).
    """
    if not content:
        raise HTTPException(400, "Empty file")

    # iPhone photos & scans come in as images — OCR the text (so it's
    # searchable), then normalize them to PDF so every document is a uniform,
    # viewable file. Both are synchronous, CPU-bound calls (Tesseract
    # subprocess, Pillow) — run off the event loop thread so a slow one can't
    # block the whole worker.
    ocr_text = ""
    if ingestion.is_image(content_type, filename):
        ocr_text = await asyncio.to_thread(ingestion.ocr_image, content)
        try:
            content, filename = await asyncio.to_thread(ingestion.convert_image_to_pdf, content, filename)
            content_type = "application/pdf"
        except Exception as e:
            raise HTTPException(422, f"Could not convert image to PDF: {e}")

    doc_id = str(uuid.uuid4())
    safe_name = safe_object_name(filename)
    storage_path = f"{user_id}/{doc_id}/{safe_name}"

    # Store the original now — the processing cron needs to read it back
    # later from a separate request, so (unlike text extraction below) this
    # can't be deferred; there's nowhere to keep the raw bytes in the
    # meantime. Best-effort: a storage failure still lets the document be
    # recorded, just flagged so the cron doesn't try to process a file it
    # can't fetch (see `_process_document`).
    try:
        await r2.upload(storage_path, content, content_type or "application/octet-stream")
    except Exception:
        storage_path = None

    # Auto-detect the course when none was supplied — the only case where
    # text has to be extracted before the response can go out.
    needs_review = False
    course_confidence: float | None = None
    if not course_id:
        text = await _extract_text_bounded(content, content_type or "", filename or "")
        if not text.strip() and ocr_text.strip():
            text = ocr_text

        courses = await supabase.select(
            "courses", columns="id,name,subject", filters={"user_id": eq(user_id)}
        )
        if courses and text.strip() and settings.has_llm:
            try:
                guess = await Archivist().classify_course(text, courses)
                course_id = guess["course_id"]
                course_confidence = guess["confidence"]
                needs_review = course_confidence < _REVIEW_CONFIDENCE_THRESHOLD
            except Exception:
                course_id = courses[0]["id"]
                course_confidence = 0.0
                needs_review = True
        elif courses:
            course_id = courses[0]["id"]
            course_confidence = 0.0
            needs_review = True
        else:
            needs_review = True  # no classes exist yet — nothing to assign

    # Create the document record (fall back to filename as a placeholder
    # title). `auto_title`/`enrich` persist what this request knew for the
    # cron to pick up later, since it runs in a completely separate request
    # with no access to this function's local variables.
    auto_title = not (title and title.strip())
    await supabase.insert("documents", {
        "id": doc_id, "user_id": user_id, "course_id": course_id,
        "title": (title or "").strip() or (filename or "Untitled"),
        "mime_type": content_type, "size_bytes": len(content),
        "storage_path": storage_path,
        "needs_review": needs_review, "course_confidence": course_confidence,
        "auto_title": auto_title, "enrich": enrich,
    })

    return {
        "id": doc_id, "course_id": course_id,
        "needs_review": needs_review, "course_confidence": course_confidence,
    }


def _stale_claim_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=_STALE_CLAIM_MINUTES)).isoformat()


async def _claim_document(doc_id: str) -> bool:
    """Atomically claim a pending document for the processing cron — an
    UPDATE conditioned on the row still actually being unclaimed (or its
    claim having gone stale), so two overlapping cron runs can't both start
    processing the same document. Mirrors app.integrations._claim's
    conditional-UPDATE pattern; returns whether *this* call won the claim."""
    claimed = await supabase.update(
        "documents", {"processing_started_at": datetime.now(timezone.utc).isoformat()},
        filters={
            "id": eq(doc_id), "ingested": eq(False), "ingest_error": "is.null",
            "or": f"(processing_started_at.is.null,processing_started_at.lt.{_stale_claim_cutoff()})",
        },
    )
    return bool(claimed)


async def _process_document(
    doc_id: str, user_id: str, storage_path: str | None, mime_type: str,
    *, enrich: bool, auto_title: bool, course_id: str | None = None, title: str | None = None,
) -> None:
    """The actual indexing + enrichment work for one claimed document:
    fetch the original back from R2, extract its text, chunk/embed, then AI
    enrichment. Only ever called from the processing cron, one document at a
    time — never inline with an upload request."""
    if not storage_path:
        await supabase.update(
            "documents",
            {"ingested": False, "ingest_error": "the original file was never stored"},
            filters={"id": eq(doc_id)},
        )
        return

    try:
        content = await r2.download(storage_path)
    except Exception as e:
        await supabase.update(
            "documents",
            {"ingested": False, "ingest_error": f"couldn't fetch the stored file: {e}"[:500]},
            filters={"id": eq(doc_id)},
        )
        return

    filename = storage_path.rsplit("/", 1)[-1]
    text = await _extract_text_bounded(content, mime_type or "", filename)

    # The document record already exists at this point, so a parsing/
    # indexing failure here shouldn't be fatal — mark it unindexed instead
    # and let the student still see/download the original file.
    try:
        await ingestion.ingest_document(doc_id, user_id, text)
    except Exception as e:
        await supabase.update(
            "documents",
            {"ingested": False, "ingest_error": str(e)[:500]},
            filters={"id": eq(doc_id)},
        )
        return

    if enrich and settings.has_llm and text.strip():
        try:
            await Archivist().enrich(user_id, doc_id, text, rename_untitled=auto_title)
        except Exception:
            pass  # enrichment (summary/keywords/importance) is best-effort

    # A document whose title/filename marks it as an "at a glance" schedule
    # (e.g. "Week at a Glance", "Unit 4 - At a Glance.pdf") gets mined for a
    # day-by-day class schedule the same way a Schoology-synced one does —
    # see app.services.schedule_extraction, shared with that sync path.
    if course_id and text.strip() and is_glance_title(title or filename):
        try:
            await apply_schedule_from_doc(
                user_id=user_id, course_id=course_id, title=title or filename,
                text=text, source="manual", source_document_id=doc_id,
            )
        except Exception:
            pass  # best-effort, same as enrichment above


@router.get("")
async def list_documents(course_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    filters = {"user_id": eq(user.id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    return await supabase.select(
        "documents",
        columns="id,title,doc_type,summary,keywords,course_id,ingested,ingest_error,size_bytes,"
                 "needs_review,course_confidence,importance,metadata,created_at",
        filters=filters, order="created_at.desc", limit=200,
    )


@router.post("/{document_id}/process")
async def process_document_now(document_id: str, user: CurrentUser = Depends(get_current_user)):
    """Index (chunk/embed) + AI-enrich one document synchronously, in this
    request. Called by the frontend right after an upload finishes, and by
    the documents page's "Index now" button for anything still pending or
    that previously failed — see the module docstring for why this replaced
    the cron as the primary path. A prior failure is cleared before
    re-claiming so a retry can actually run instead of being skipped as
    already-errored (the cron intentionally never auto-retries a failure;
    this endpoint, triggered by a person, is the deliberate retry path)."""
    rows = await supabase.select(
        "documents",
        columns="id,course_id,title,storage_path,mime_type,enrich,auto_title,ingested,ingest_error",
        filters={"user_id": eq(user.id), "id": eq(document_id)}, limit=1,
    )
    if not rows:
        raise HTTPException(404, "Not found")
    doc = rows[0]
    if doc["ingested"]:
        return {"id": document_id, "ingested": True, "ingest_error": None}

    if doc.get("ingest_error"):
        await supabase.update(
            "documents", {"ingest_error": None, "processing_started_at": None},
            filters={"id": eq(document_id)},
        )

    if not await _claim_document(document_id):
        raise HTTPException(409, "Already being processed")

    await _process_document(
        document_id, user.id, doc.get("storage_path"), doc.get("mime_type") or "",
        enrich=doc.get("enrich", True), auto_title=doc.get("auto_title", False),
        course_id=doc.get("course_id"), title=doc.get("title"),
    )

    rows = await supabase.select(
        "documents", columns="id,ingested,ingest_error",
        filters={"id": eq(document_id)}, limit=1,
    )
    return rows[0] if rows else {"id": document_id}


@router.get("/{document_id}")
async def get_document(document_id: str, user: CurrentUser = Depends(get_current_user)):
    rows = await supabase.select(
        "documents", filters={"user_id": eq(user.id), "id": eq(document_id)}, limit=1
    )
    if not rows:
        raise HTTPException(404, "Not found")
    doc = rows[0]
    if doc.get("storage_path"):
        try:
            doc["download_url"] = r2.signed_url(doc["storage_path"])
        except Exception:
            doc["download_url"] = None
    return doc


@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    course_id: str = Form(...),
    title: str | None = Form(default=None),
    enrich: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),
):
    if not (course_id and course_id.strip()):
        raise HTTPException(422, "A course is required for every document.")
    content = await file.read()
    result = await _prepare_and_store(
        user_id=user.id, course_id=course_id, content=content,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        title=title, enrich=enrich,
    )
    return {**result, "processing": True}


@router.post("/bulk-upload", status_code=201)
async def bulk_upload_documents(
    files: list[UploadFile] = File(...),
    enrich: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),
):
    """Drop multiple files at once with no class picked up front.

    Each file's class is auto-detected from the student's existing classes
    (by content). A file is always assigned a class — even a low-confidence
    guess — but guesses under the confidence threshold come back with
    ``needs_review: true`` so the student can fix them from the documents page.
    Indexing + AI enrichment for every file run on the processing cron, same
    as a single upload — the response comes back once each file is stored
    and classified, not once every file is fully indexed.
    """
    if not files:
        raise HTTPException(400, "No files provided.")
    results = []
    for file in files:
        content = await file.read()
        try:
            result = await _prepare_and_store(
                user_id=user.id, course_id=None, content=content,
                filename=file.filename or "document",
                content_type=file.content_type or "application/octet-stream",
                title=None, enrich=enrich,
            )
            results.append({"filename": file.filename, **result})
        except HTTPException as e:
            results.append({"filename": file.filename, "error": str(e.detail)})
        except Exception as e:
            results.append({"filename": file.filename, "error": str(e)[:300]})
    return {"results": results}


@router.patch("/{document_id}")
async def update_document(
    document_id: str, body: DocumentPatchRequest, user: CurrentUser = Depends(get_current_user),
):
    """Used by the bulk-upload review screen to correct an auto-detected
    course, and by the documents page to rename a document or override its
    importance rating."""
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No fields to update.")
    # Assigning/confirming a course clears the review flag unless the caller
    # explicitly says otherwise.
    if "course_id" in patch and "needs_review" not in patch:
        patch["needs_review"] = False
    # A human set this, not the Archivist — record that so a later
    # re-enrichment (see Archivist.enrich) never quietly overwrites it.
    if "importance" in patch:
        patch["importance_source"] = "manual"
    rows = await supabase.update(
        "documents", patch, filters={"user_id": eq(user.id), "id": eq(document_id)}
    )
    if not rows:
        raise HTTPException(404, "Not found")
    return rows[0]


@router.post("/import-drive", status_code=201)
async def import_from_drive(body: DriveImportRequest, user: CurrentUser = Depends(get_current_user)):
    """Import a file the user selected in the Google Drive picker.

    The frontend obtains a short-lived OAuth access token via the Google
    Picker; we download the bytes server-side and run the same pipeline as a
    direct upload. Native Google Docs/Sheets/Slides are exported to a portable
    format first.
    """
    if not (body.course_id and body.course_id.strip()):
        raise HTTPException(422, "A course is required for every document.")

    mime = (body.mime_type or "").lower()
    export_map = {
        "application/vnd.google-apps.document": (
            "application/pdf", ".pdf"),
        "application/vnd.google-apps.presentation": (
            "application/pdf", ".pdf"),
        "application/vnd.google-apps.spreadsheet": (
            "text/csv", ".csv"),
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        if mime in export_map:
            export_mime, ext = export_map[mime]
            url = f"https://www.googleapis.com/drive/v3/files/{body.file_id}/export"
            params = {"mimeType": export_mime}
            filename = (body.name or "drive-file") + ext
            content_type = export_mime
        else:
            url = f"https://www.googleapis.com/drive/v3/files/{body.file_id}"
            params = {"alt": "media"}
            filename = body.name or "drive-file"
            content_type = body.mime_type or "application/octet-stream"
        r = await client.get(
            url, params=params,
            headers={"Authorization": f"Bearer {body.access_token}"},
        )
    if r.status_code >= 300:
        raise HTTPException(502, f"Google Drive download failed ({r.status_code}): {r.text[:200]}")

    result = await _prepare_and_store(
        user_id=user.id, course_id=body.course_id, content=r.content,
        filename=filename, content_type=content_type, title=body.name, enrich=body.enrich,
    )
    return {**result, "processing": True}


@router.post("/ingest-text", status_code=201)
async def ingest_text(body: IngestTextRequest, user: CurrentUser = Depends(get_current_user)):
    """Ingest raw text (notes pasted directly, or content from an integration)."""
    doc_id = str(uuid.uuid4())
    await supabase.insert("documents", {
        "id": doc_id, "user_id": user.id, "course_id": body.course_id,
        "title": body.title, "doc_type": body.doc_type, "size_bytes": len(body.text),
    })
    report = await ingestion.ingest_document(doc_id, user.id, body.text)
    enrichment = None
    if body.enrich and settings.has_llm and body.text.strip():
        try:
            enrichment = await Archivist().enrich(user.id, doc_id, body.text)
        except Exception as e:
            enrichment = {"error": str(e)}
    return {"id": doc_id, "chunks": report.get("chunks", 0), "enrichment": enrichment}


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: str, user: CurrentUser = Depends(get_current_user)):
    rows = await supabase.select(
        "documents", columns="storage_path",
        filters={"user_id": eq(user.id), "id": eq(document_id)}, limit=1,
    )
    # The document disappears from the app immediately (the row goes now),
    # but the underlying R2 file itself isn't removed inline — it's queued
    # for a scheduled sweep after a 24-hour grace window (see
    # app.services.storage_cleanup) instead, so a delete isn't instantly
    # unrecoverable.
    storage_path = rows[0].get("storage_path") if rows else None
    await supabase.delete("documents", filters={"user_id": eq(user.id), "id": eq(document_id)})
    if storage_path:
        await storage_cleanup.queue_deletion(storage_path)
    return None


async def _process_pending_documents() -> dict:
    candidates = await supabase.select(
        "documents", columns="id,user_id,course_id,title,storage_path,mime_type,enrich,auto_title",
        filters={
            "ingested": eq(False), "ingest_error": "is.null",
            "or": f"(processing_started_at.is.null,processing_started_at.lt.{_stale_claim_cutoff()})",
        },
        limit=_PROCESS_BATCH_SIZE,
    ) or []

    processed, skipped, errors = 0, 0, 0
    for row in candidates:
        if not await _claim_document(row["id"]):
            skipped += 1
            continue
        try:
            await _process_document(
                row["id"], row["user_id"], row.get("storage_path"), row.get("mime_type") or "",
                enrich=row.get("enrich", True), auto_title=row.get("auto_title", False),
                course_id=row.get("course_id"), title=row.get("title"),
            )
            processed += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            await supabase.update(
                "documents", {"ingested": False, "ingest_error": str(e)[:500]},
                filters={"id": eq(row["id"])},
            )
    return {"checked": len(candidates), "processed": processed, "skipped": skipped, "errors": errors}


@router.post("/cron/process-pending")
@router.get("/cron/process-pending")
async def process_pending_documents(request: Request):
    """Safety-net sweep that finishes indexing (chunk/embed) + AI-enriching
    any document still sitting at ``ingested: false`` — normally the
    frontend's post-upload call to `POST /documents/{id}/process` (or the
    "Index now" button) already does this; this only catches documents
    neither of those ever reached (tab closed mid-upload, network drop,
    etc.), so it runs at a low frequency, not the every-couple-minutes
    cadence this used to need when it was the only path — see the module
    docstring. Claims up to `_PROCESS_BATCH_SIZE` documents per call. GET:
    Vercel Cron Jobs always invoke via GET. POST: kept for n8n/curl/other
    schedulers that prefer it. Secured by ATLAS_CRON_SECRET, same as the
    other cron routes — see `check_cron_secret`."""
    check_cron_secret(request)
    return await _process_pending_documents()


@router.post("/cron/purge-deleted")
@router.get("/cron/purge-deleted")
async def purge_deleted_documents(request: Request):
    """Scheduled sweep that actually removes from R2 whatever `delete_document`
    queued more than `storage_cleanup.GRACE_PERIOD_HOURS` ago. GET: Vercel Cron
    Jobs always invoke via GET. POST: kept for n8n/curl/other schedulers that
    prefer it — both do the same thing. Secured by ATLAS_CRON_SECRET, same as
    the integrations sync cron routes — see `check_cron_secret`."""
    check_cron_secret(request)
    return await storage_cleanup.purge_expired()
