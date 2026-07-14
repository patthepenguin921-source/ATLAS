"""Documents — upload, ingest (chunk+embed), enrich, and manage files."""
from __future__ import annotations

import re
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.agents.archivist import Archivist
from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import DriveImportRequest, IngestTextRequest
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


async def _store_and_ingest(
    *, user_id: str, course_id: str, content: bytes, filename: str,
    content_type: str, title: str | None, enrich: bool,
) -> dict:
    """Shared pipeline: (convert images→PDF) → store → record → chunk/embed → enrich.

    Used by both direct upload and Google Drive import so they behave
    identically. ``title`` is optional; when omitted, the Archivist generates
    one from the document's content.
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
        await supabase.upload(
            settings.atlas_storage_bucket, storage_path, content,
            content_type or "application/octet-stream",
        )
    except Exception:
        storage_path = None

    # 2) create the document record (fall back to filename as a placeholder title)
    auto_title = not (title and title.strip())
    await supabase.insert("documents", {
        "id": doc_id, "user_id": user_id, "course_id": course_id,
        "title": (title or "").strip() or (filename or "Untitled"),
        "mime_type": content_type, "size_bytes": len(content),
        "storage_path": storage_path,
    })

    # 3) extract + ingest (chunk + embed). A converted photo has no PDF text
    # layer, so fall back to the OCR'd text for images. The original is
    # already saved and the document record already exists at this point, so
    # a parsing/indexing failure here shouldn't fail the whole upload — mark
    # the document unindexed instead and let the user still see/download it.
    text = ""
    try:
        text = ingestion.extract_text(content, content_type or "", filename or "")
        if not text.strip() and ocr_text.strip():
            text = ocr_text
        report = await ingestion.ingest_document(doc_id, user_id, text)
    except Exception as e:
        await supabase.update(
            "documents",
            {"ingested": False, "ingest_error": str(e)[:500]},
            filters={"id": eq(doc_id)},
        )
        report = {"chunks": 0}

    # 4) enrich metadata + knowledge-graph links (best-effort, needs an LLM)
    enrichment = None
    if enrich and settings.has_llm and text.strip():
        try:
            enrichment = await Archivist().enrich(
                user_id, doc_id, text, rename_untitled=auto_title
            )
        except Exception as e:
            enrichment = {"error": str(e)}

    return {"id": doc_id, "chunks": report.get("chunks", 0), "enrichment": enrichment}


@router.get("")
async def list_documents(course_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    filters = {"user_id": eq(user.id)}
    if course_id:
        filters["course_id"] = eq(course_id)
    return await supabase.select(
        "documents",
        columns="id,title,doc_type,summary,keywords,course_id,ingested,size_bytes,created_at",
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
            doc["download_url"] = await supabase.signed_url(
                settings.atlas_storage_bucket, doc["storage_path"]
            )
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
    await supabase.delete("documents", filters={"user_id": eq(user.id), "id": eq(document_id)})
    return None
