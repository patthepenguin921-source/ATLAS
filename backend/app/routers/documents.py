"""Documents — upload, ingest (chunk+embed), enrich, and manage files."""
from __future__ import annotations

import asyncio
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile

from app.agents.archivist import Archivist
from app.config import settings
from app.core.r2_client import r2, safe_object_name
from app.core.security import CurrentUser, check_cron_secret, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import DocumentPatchRequest, DriveImportRequest, IngestTextRequest
from app.services import ingestion, storage_cleanup

router = APIRouter(prefix="/documents", tags=["documents"])

# Below this, a course auto-detection guess is flagged for the student to
# double-check rather than trusted outright.
_REVIEW_CONFIDENCE_THRESHOLD = 0.6


def _schedule_finish(background_tasks: BackgroundTasks, result: dict, user_id: str, enrich: bool) -> None:
    """Queue `_finish_ingest` with the fields `_prepare_and_store` produced —
    shared by all three upload paths so none of them has to spell out the
    same eight-argument call."""
    background_tasks.add_task(
        _finish_ingest, result["id"], user_id, result["content"], result["filename"],
        result["content_type"], result["storage_path"], result["text"], result["ocr_text"],
        enrich=enrich, auto_title=result["auto_title"],
    )


async def _prepare_and_store(
    *, user_id: str, course_id: str | None, content: bytes, filename: str,
    content_type: str, title: str | None,
) -> dict:
    """Fast half of the pipeline: (convert images→PDF) → classify → record.
    Returns immediately once the document row exists — with ``ingested:
    false`` ("pending" in the UI) — so the caller can send the upload
    response back right away. ``_finish_ingest`` (the slow half — storing the
    original to R2, extracting its text if that wasn't already needed here,
    chunking/embedding, and AI enrichment) runs afterward as a background
    task; none of that has to block the response.

    Used by direct upload, bulk upload, and Google Drive import so they all
    behave identically. ``title`` is optional; when omitted, the Archivist
    generates one from the document's content. ``course_id`` is optional —
    when omitted (bulk upload), the course is auto-detected from the
    student's existing classes and low-confidence guesses are flagged via
    ``needs_review`` instead of being left unfiled. Auto-detection needs the
    document's text, so bulk upload is the one case that still extracts text
    synchronously here — a direct upload already has its course, so text
    extraction (the slow part for a big PDF) is deferred entirely.
    """
    if not content:
        raise HTTPException(400, "Empty file")

    # iPhone photos & scans come in as images — OCR the text (so it's
    # searchable), then normalize them to PDF so every document is a uniform,
    # viewable file. Both are synchronous, CPU-bound calls (Tesseract
    # subprocess, Pillow) — run off the event loop thread so a slow one can't
    # block the whole worker. Kept synchronous (unlike PDF text extraction
    # below) because it determines the actual stored filename/content-type
    # the document row needs, and is comparatively fast either way.
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

    # Auto-detect the course when none was supplied — the only case where
    # text has to be extracted before the response can go out.
    text = ""
    needs_review = False
    course_confidence: float | None = None
    if not course_id:
        try:
            text = await asyncio.to_thread(ingestion.extract_text, content, content_type or "", filename or "")
            if not text.strip() and ocr_text.strip():
                text = ocr_text
        except Exception:
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
    # title). `storage_path` is recorded now even though the upload to R2
    # hasn't happened yet (that's in `_finish_ingest`) — it's a deterministic
    # key, and `get_document`'s signed-download-URL lookup degrades fine if
    # asked for it a moment before the background upload actually lands.
    auto_title = not (title and title.strip())
    await supabase.insert("documents", {
        "id": doc_id, "user_id": user_id, "course_id": course_id,
        "title": (title or "").strip() or (filename or "Untitled"),
        "mime_type": content_type, "size_bytes": len(content),
        "storage_path": storage_path,
        "needs_review": needs_review, "course_confidence": course_confidence,
    })

    return {
        "id": doc_id, "content": content, "filename": filename,
        "content_type": content_type, "storage_path": storage_path,
        "text": text, "ocr_text": ocr_text, "auto_title": auto_title,
        "course_id": course_id, "needs_review": needs_review,
        "course_confidence": course_confidence,
    }


async def _finish_ingest(
    doc_id: str, user_id: str, content: bytes, filename: str, content_type: str,
    storage_path: str, text: str, ocr_text: str, *, enrich: bool, auto_title: bool,
) -> None:
    """Slow half of the pipeline — store the original to R2, extract its text
    if a course was already given (so `_prepare_and_store` skipped it),
    chunk/embed, then AI enrichment. Runs as a background task after the
    upload response has already gone back to the client; the document stays
    ``ingested: false`` ("pending" in the UI) until this finishes, so
    re-fetching the documents list shows live progress instead of the
    request just hanging."""
    try:
        await r2.upload(storage_path, content, content_type or "application/octet-stream")
    except Exception:
        await supabase.update(
            "documents", {"storage_path": None}, filters={"id": eq(doc_id)}
        )

    if not text.strip():
        try:
            text = await asyncio.to_thread(ingestion.extract_text, content, content_type or "", filename or "")
            if not text.strip() and ocr_text.strip():
                text = ocr_text
        except Exception:
            text = ocr_text

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


@router.get("")
async def list_documents(course_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    filters = {"user_id": eq(user.id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    return await supabase.select(
        "documents",
        columns="id,title,doc_type,summary,keywords,course_id,ingested,ingest_error,size_bytes,"
                 "needs_review,course_confidence,importance,created_at",
        filters=filters, order="created_at.desc", limit=200,
    )


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
    background_tasks: BackgroundTasks,
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
        title=title,
    )
    _schedule_finish(background_tasks, result, user.id, enrich)
    return {
        "id": result["id"], "course_id": result["course_id"],
        "needs_review": result["needs_review"],
        "course_confidence": result["course_confidence"],
        "processing": True,
    }


@router.post("/bulk-upload", status_code=201)
async def bulk_upload_documents(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    enrich: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),
):
    """Drop multiple files at once with no class picked up front.

    Each file's class is auto-detected from the student's existing classes
    (by content). A file is always assigned a class — even a low-confidence
    guess — but guesses under the confidence threshold come back with
    ``needs_review: true`` so the student can fix them from the documents page.
    Chunk/embed + AI enrichment for every file runs in the background, same
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
                title=None,
            )
            _schedule_finish(background_tasks, result, user.id, enrich)
            results.append({
                "filename": file.filename, "id": result["id"],
                "course_id": result["course_id"], "needs_review": result["needs_review"],
                "course_confidence": result["course_confidence"],
            })
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
async def import_from_drive(
    body: DriveImportRequest, background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
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
        filename=filename, content_type=content_type, title=body.name,
    )
    _schedule_finish(background_tasks, result, user.id, body.enrich)
    return {
        "id": result["id"], "course_id": result["course_id"],
        "needs_review": result["needs_review"],
        "course_confidence": result["course_confidence"],
        "processing": True,
    }


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
