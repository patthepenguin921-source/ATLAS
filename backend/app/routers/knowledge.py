"""Knowledge — concepts, the knowledge graph, and the student knowledge model."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user
from app.core.supabase_client import eq, supabase
from app.schemas import KnowledgeReviewRequest
from app.services import knowledge_model, memory

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/concepts")
async def concepts(user: CurrentUser = Depends(get_current_user)):
    return await supabase.select(
        "concepts", columns="id,name,description,subject,course_id",
        filters={"user_id": eq(user.id)}, order="name.asc", limit=500,
    )


@router.get("/graph")
async def graph(user: CurrentUser = Depends(get_current_user)):
    """Return the knowledge graph (nodes + edges) for visualization."""
    nodes = await supabase.select(
        "concepts", columns="id,name,subject,course_id",
        filters={"user_id": eq(user.id)}, limit=500,
    ) or []
    edges = await supabase.select(
        "concept_edges", columns="from_concept,to_concept,edge_type,weight",
        filters={"user_id": eq(user.id)}, limit=2000,
    ) or []
    # attach the student model where present
    km = await supabase.select(
        "student_knowledge", columns="concept_id,mastery,retention,confidence",
        filters={"user_id": eq(user.id)}, limit=500,
    ) or []
    km_map = {k["concept_id"]: k for k in km}
    for n in nodes:
        n["knowledge"] = km_map.get(n["id"])
    return {"nodes": nodes, "edges": edges}


@router.post("/edges", status_code=201)
async def create_edge(
    from_concept: str, to_concept: str, edge_type: str = "related",
    user: CurrentUser = Depends(get_current_user),
):
    created = await supabase.insert("concept_edges", {
        "user_id": user.id, "from_concept": from_concept,
        "to_concept": to_concept, "edge_type": edge_type,
    }, upsert=True)
    return created[0] if created else None


@router.get("/model")
async def student_model(user: CurrentUser = Depends(get_current_user)):
    rows = await supabase.select(
        "student_knowledge",
        columns="concept_id,confidence,mastery,retention,next_review_at,predicted_forget_at,evidence_count",
        filters={"user_id": eq(user.id)}, order="retention.asc", limit=500,
    ) or []
    ids = ",".join(r["concept_id"] for r in rows)
    if ids:
        names = await supabase.select("concepts", columns="id,name",
                                      filters={"id": f"in.({ids})"}) or []
        name_map = {c["id"]: c["name"] for c in names}
        for r in rows:
            r["concept_name"] = name_map.get(r["concept_id"])
    return rows


@router.get("/review-queue")
async def review_queue(user: CurrentUser = Depends(get_current_user)):
    """Concepts due for spaced-repetition review right now."""
    return await memory.concepts_needing_review(user.id, limit=30)


@router.post("/review")
async def review(body: KnowledgeReviewRequest, user: CurrentUser = Depends(get_current_user)):
    """Record a review outcome; reschedules via SM-2 and updates mastery."""
    return await knowledge_model.review(
        user.id, body.concept_id, body.quality, confidence=body.confidence
    )


@router.post("/refresh-retention")
async def refresh_retention(user: CurrentUser = Depends(get_current_user)):
    count = await knowledge_model.refresh_retention(user.id)
    return {"updated": count}
