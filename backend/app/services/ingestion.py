"""Document ingestion — turns uploaded files into searchable knowledge.

Pipeline: extract text → chunk → embed → store chunks (pgvector). Optionally
the Archivist agent enriches the document with a summary, keywords, and concept
links. Nothing is lost; everything becomes semantic memory.
"""
from __future__ import annotations

import io
from typing import Any

from app.core.supabase_client import eq, supabase
from app.embeddings.embedder import embed_texts

CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------
def extract_text(content: bytes, mime_type: str, filename: str = "") -> str:
    name = (filename or "").lower()
    mt = (mime_type or "").lower()
    try:
        if "pdf" in mt or name.endswith(".pdf"):
            return _extract_pdf(content)
        if "presentation" in mt or name.endswith((".pptx", ".ppt")):
            return _extract_pptx(content)
    except Exception:
        pass  # fall through to plain-text decode
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_pptx(content: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(content))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        texts = [sh.text for sh in slide.shapes if getattr(sh, "has_text_frame", False)]
        if texts:
            parts.append(f"[Slide {i}] " + "\n".join(texts))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        # try to break on a paragraph/sentence boundary
        if end < len(text):
            for sep in ("\n\n", "\n", ". "):
                cut = text.rfind(sep, start + size // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [c for c in chunks if c]


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------
async def ingest_document(document_id: str, user_id: str, text: str) -> dict[str, Any]:
    """Chunk + embed + persist. Returns a small ingestion report."""
    # clear any prior chunks (re-ingest safe)
    await supabase.delete("document_chunks", filters={"document_id": eq(document_id)})

    chunks = chunk_text(text)
    if not chunks:
        await supabase.update(
            "documents",
            {"ingested": True, "ingest_error": "no extractable text", "extracted_text": text[:200000]},
            filters={"id": eq(document_id)},
        )
        return {"chunks": 0}

    embeddings = await embed_texts(chunks)
    rows = [
        {
            "user_id": user_id,
            "document_id": document_id,
            "chunk_index": i,
            "content": c,
            "token_count": max(1, len(c) // 4),
            "embedding": emb,
        }
        for i, (c, emb) in enumerate(zip(chunks, embeddings))
    ]
    # insert in batches to keep payloads reasonable
    for i in range(0, len(rows), 50):
        await supabase.insert("document_chunks", rows[i : i + 50])

    await supabase.update(
        "documents",
        {"ingested": True, "ingest_error": None, "extracted_text": text[:200000],
         "page_count": None},
        filters={"id": eq(document_id)},
    )
    return {"chunks": len(rows)}
