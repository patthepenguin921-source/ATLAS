"""Search — semantic + structured, answering questions instantly from memory."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.llm import claude
from app.schemas import SearchRequest
from app.services import memory

router = APIRouter(prefix="/search", tags=["search"])


def _snippet(text: str, query: str, *, radius: int = 80) -> str:
    """A short excerpt of `text` centered on the first match of `query`, so a
    body-text hit shows *why* it matched instead of just the document title."""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


@router.post("/semantic")
async def semantic(body: SearchRequest, user: CurrentUser = Depends(get_current_user)):
    results = await memory.semantic_search(
        user.id, body.query, limit=body.limit, course_id=body.course_id,
        threshold=body.threshold,
    )
    return {"query": body.query, "results": results}


@router.post("/ask")
async def ask(body: SearchRequest, user: CurrentUser = Depends(get_current_user)):
    """Natural-language question answered with grounded citations.

    e.g. "What mistakes do I keep making in AP Calculus?",
         "Show every assignment related to photosynthesis."
    """
    ctx = await memory.build_context(user.id, body.query, semantic_limit=body.limit)
    context_text = memory.render_context(ctx)
    system = (
        "You are Atlas's search intelligence. Answer the student's question "
        "using ONLY the retrieved context below. Cite document titles when you "
        "use a passage. If the answer isn't in the context, say what's missing.\n\n"
        + context_text
    )
    answer = await claude.complete(
        system=system,
        messages=[{"role": "user", "content": body.query}],
        max_tokens=1000,
    )
    return {
        "query": body.query,
        "answer": answer,
        "sources": [
            {"document_title": p.get("document_title"), "similarity": p.get("similarity")}
            for p in ctx.get("relevant_passages", [])
        ],
    }


@router.get("/text")
async def text_search(
    q: str = "", limit: int = 20, course_id: str | None = None,
    doc_type: str | None = None, category: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Fast structured text search across assignments + documents (trigram) —
    documents match on title OR their extracted body text (`extracted_text`,
    already trigram-indexed — see `idx_documents_text_trgm`), so a search for
    a term that only appears inside the file still finds it.

    `course_id` scopes both lists to one class (used by the course detail
    page's own document search). `doc_type`/`category` filter documents/
    assignments respectively by their existing tag — e.g. narrowing to just
    `glance` schedule docs or just `quiz` assignments — and can be used with
    an empty `q` to just browse everything tagged that way.
    """
    assignment_filters = {"user_id": eq(user.id)}
    if q:
        assignment_filters["title"] = f"ilike.*{q}*"
    if course_id:
        assignment_filters["course_id"] = eq(course_id)
    if category:
        assignment_filters["category"] = eq(category)
    assignments = (
        await supabase.select(
            "assignments", columns="id,title,category,course_id,due_date",
            filters=assignment_filters, limit=limit,
        ) if (q or category) else []
    ) or []

    doc_filters = {"user_id": eq(user.id)}
    if course_id:
        doc_filters["course_id"] = eq(course_id)
    if doc_type:
        doc_filters["doc_type"] = eq(doc_type)

    if not q:
        documents = await supabase.select(
            "documents", columns="id,title,doc_type,course_id,ingested",
            filters=doc_filters, order="created_at.desc", limit=limit,
        ) or []
        return {"assignments": assignments, "documents": documents}

    title_matches = await supabase.select(
        "documents", columns="id,title,doc_type,course_id,ingested",
        filters={**doc_filters, "title": f"ilike.*{q}*"}, limit=limit,
    ) or []
    content_matches = await supabase.select(
        "documents", columns="id,title,doc_type,course_id,ingested,extracted_text",
        filters={**doc_filters, "extracted_text": f"ilike.*{q}*"}, limit=limit,
    ) or []

    documents = list(title_matches)
    seen_ids = {d["id"] for d in title_matches}
    for d in content_matches:
        if d["id"] in seen_ids or len(documents) >= limit:
            continue
        seen_ids.add(d["id"])
        snippet = _snippet(d.pop("extracted_text", "") or "", q)
        documents.append({**d, "snippet": snippet})

    return {"assignments": assignments, "documents": documents}
