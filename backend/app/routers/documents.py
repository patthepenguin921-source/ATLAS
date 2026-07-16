"""Documents — upload, ingest (chunk+embed), enrich, and manage files."""
from __future__ import annotations

import re
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.agents.archivist import Archivist
from app.config import settings
from app.core.r2_client import r2
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import DocumentPatchRequest, DriveImportRequest, IngestTextRequest
from app.services import ingestion

router = APIRouter(prefix="/documents", tags=["documents"])

# Supabase Storage only accepts a restricted, mostly-ASCII charset in object
# keys (word chars, and !-.*'()  &$@=;:+,?). Filenames with em/en dashes,
# curly quotes, accented letters, emoji, etc. make the upload 400 — which
# we treat as best-effort and swallow — silently losing the original file.
# Replace anything outside that charset instead of dropping the file.
_UNSAFE_STORAGE_CHARS = re.compile(r"[^\w!\-.*'() &$@=;:+,?]")


def _safe_storage_name(filename: str) -> str:
    name = (filename or "document").replace("/", "_")
    return _UNSAFE_STORAGE_CHARS.sub("_", name) or "document"


# Below this, a course auto-detection guess is flagged for the student to
# double-check rather than trusted outright.
_REVIEW_CONFIDENCE_THRESHOLD = 0.6


async def _store_and_ingest(
    *, user_id: str, course_id: str | None, content: bytes, filename: str,
    content_type: str, title: str | None, enrich: bool,
) -> dict:
    """Shared pipeline: (convert images→PDF) → store → classify → record →
    chunk/embed → enrich.

    Used by direct upload, bulk upload, and Google Drive import so they all
    behave identically. ``title`` is optional; when omitted, the Archivist
    generates one from the document's content. ``course_id`` is optional —
    when omitted (bulk upload), the course is auto-detected from the
    student's existing classes and low-confidence guesses are flagged via
    ``needs_review`` instead of being left unfiled.
    """
    if not content:
        raise HTTPException(400, "Empty file")

    # iPhone photos & scans come in as images — OCR the text (so it's
    # searchable), then normalize them to PDF so every document is a uniform,
    # viewable file.
    ocr_text = ""
    if ingestion.is_image(content_type, filename):
        ocr_text = ingestion.ocr_image(content)
        try:
            content, filename = ingestion.convert_image_to_pdf(content, filename)
            content_type = "application/pdf"
        except Exception as e:
            raise HTTPException(422, f"Could not convert image to PDF: {e}")

    doc_id = str(uuid.uuid4())
    safe_name = _safe_storage_name(filename)
    storage_path = f"{user_id}/{doc_id}/{safe_name}"

    # 1) store the original file (best-effort; text ingest still works without it)
    try:
        await r2.upload(storage_path, content, content_type or "application/octet-stream")
    except Exception:
        storage_path = None

    # 2) extract text up front — needed both for chunk/embed and (when no
    # course was given) for auto-detecting which class this belongs to.
    # Parsing failures are non-fatal; the original is already saved either way.
    text = ""
    try:
        text = ingestion.extract_text(content, content_type or "", filename or "")
        if not text.strip() and ocr_text.strip():
            text = ocr_text
    except Exception:
        text = ocr_text

    # 2b) auto-detect the course when none was supplied
    needs_review = False
    course_confidence: float | None = None
    if not course_id:
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

    # 3) create the document record (fall back to filename as a placeholder title)
    auto_title = not (title and title.strip())
    await supabase.insert("documents", {
        "id": doc_id, "user_id": user_id, "course_id": course_id,
        "title": (title or "").strip() or (filename or "Untitled"),
        "mime_type": content_type, "size_bytes": len(content),
        "storage_path": storage_path,
        "needs_review": needs_review, "course_confidence": course_confidence,
    })

    # 4) chunk + embed. The document record already exists at this point, so
    # a parsing/indexing failure here shouldn't fail the whole upload — mark
    # the document unindexed instead and let the user still see/download it.
    try:
        report = await ingestion.ingest_document(doc_id, user_id, text)
    except Exception as e:
        await supabase.update(
            "documents",
            {"ingested": False, "ingest_error": str(e)[:500]},
            filters={"id": eq(doc_id)},
        )
        report = {"chunks": 0}

    # 5) enrich metadata + knowledge-graph links (best-effort, needs an LLM)
    enrichment = None
    if enrich and settings.has_llm and text.strip():
        try:
            enrichment = await Archivist().enrich(
                user_id, doc_id, text, rename_untitled=auto_title
            )
        except Exception as e:
            enrichment = {"error": str(e)}

    return {
        "id": doc_id, "chunks": report.get("chunks", 0), "enrichment": enrichment,
        "course_id": course_id, "needs_review": needs_review,
        "course_confidence": course_confidence,
    }


@router.get("")
async def list_documents(course_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    filters = {"user_id": eq(user.id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    return await supabase.select(
        "documents",
        columns="id,title,doc_type,summary,keywords,course_id,ingested,size_bytes,"
                 "needs_review,course_confidence,created_at",
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
    file: UploadFile = File(...),
    course_id: str = Form(...),
    title: str | None = Form(default=None),
    enrich: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),
):
    if not (course_id and course_id.strip()):
        raise HTTPException(422, "A course is required for every document.")
    content = await file.read()
    return await _store_and_ingest(
        user_id=user.id, course_id=course_id, content=content,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        title=title, enrich=enrich,
    )


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
    """
    if not files:
        raise HTTPException(400, "No files provided.")
    results = []
    for file in files:
        content = await file.read()
        try:
            result = await _store_and_ingest(
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
    """Used by the bulk-upload review screen to correct an auto-detected course."""
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No fields to update.")
    # Assigning/confirming a course clears the review flag unless the caller
    # explicitly says otherwise.
    if "course_id" in patch and "needs_review" not in patch:
        patch["needs_review"] = False
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

    return await _store_and_ingest(
        user_id=user.id, course_id=body.course_id, content=r.content,
        filename=filename, content_type=content_type,
        title=body.name, enrich=body.enrich,
    )


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
    # Remove the file before the row so a storage failure leaves the document
    # (and its file) intact for retry instead of orphaning the file forever.
    storage_path = rows[0].get("storage_path") if rows else None
    if storage_path:
        await r2.remove(storage_path)
    await supabase.delete("documents", filters={"user_id": eq(user.id), "id": eq(document_id)})
    return None
