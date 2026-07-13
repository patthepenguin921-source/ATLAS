"""Search — semantic + structured, answering questions instantly from memory."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.llm import claude
from app.schemas import SearchRequest
from app.services import memory

router = APIRouter(prefix="/search", tags=["search"])


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
async def text_search(q: str, limit: int = 20, user: CurrentUser = Depends(get_current_user)):
    """Fast structured text search across assignments + documents (trigram)."""
    assignments = await supabase.select(
        "assignments", columns="id,title,category,course_id,due_date",
        filters={"user_id": eq(user.id), "title": f"ilike.*{q}*"}, limit=limit,
    ) or []
    documents = await supabase.select(
        "documents", columns="id,title,doc_type,course_id",
        filters={"user_id": eq(user.id), "title": f"ilike.*{q}*"}, limit=limit,
    ) or []
    return {"assignments": assignments, "documents": documents}
