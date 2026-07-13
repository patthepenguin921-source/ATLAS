"""Documents — upload, ingest (chunk+embed), enrich, and manage files."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.agents.archivist import Archivist
from app.config import settings
from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import IngestTextRequest
from app.services import ingestion

router = APIRouter(prefix="/documents", tags=["documents"])


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
    course_id: str | None = Form(default=None),
    title: str | None = Form(default=None),
    enrich: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    doc_id = str(uuid.uuid4())
    safe_name = (file.filename or "document").replace("/", "_")
    storage_path = f"{user.id}/{doc_id}/{safe_name}"

    # 1) store the original file
    try:
        await supabase.upload(
            settings.atlas_storage_bucket, storage_path, content,
            file.content_type or "application/octet-stream",
        )
    except Exception as e:
        # storage is best-effort; we can still ingest text
        storage_path = None

    # 2) create the document record
    row = {
        "id": doc_id, "user_id": user.id, "course_id": course_id,
        "title": title or (file.filename or "Untitled"),
        "mime_type": file.content_type, "size_bytes": len(content),
        "storage_path": storage_path,
    }
    await supabase.insert("documents", row)

    # 3) extract + ingest (chunk + embed)
    text = ingestion.extract_text(content, file.content_type or "", file.filename or "")
    report = await ingestion.ingest_document(doc_id, user.id, text)

    # 4) enrich metadata + knowledge-graph links (best-effort, needs Claude)
    enrichment = None
    if enrich and settings.has_llm and text.strip():
        try:
            enrichment = await Archivist().enrich(user.id, doc_id, text)
        except Exception as e:
            enrichment = {"error": str(e)}

    return {"id": doc_id, "chunks": report.get("chunks", 0), "enrichment": enrichment}


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
