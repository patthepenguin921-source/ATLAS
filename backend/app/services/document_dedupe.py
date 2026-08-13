"""Detects documents that likely represent the same file added twice under
separate rows -- most commonly the same PDF pulled in once via Google Drive
import and again via a manual upload, or the same handout uploaded twice
from different devices -- so the student can review and merge them from the
documents page instead of ending up with duplicate/split search results and
two "processing" rows for what's really one file.

Deliberately conservative, mirroring `app.services.assignment_dedupe`: two
documents only get suggested when they're filed the same way (same course,
or both unfiled as "General") and have a *high* title similarity. A missed
duplicate just means one extra row on the page; a wrong merge silently
combines two different files' content, which is much worse.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from app.core.supabase_client import eq, supabase
from app.services import storage_cleanup

# Fields copied onto the kept row from the discarded one when the kept row
# doesn't already have a value -- so merging never loses detail that only
# made it onto one side (e.g. the Drive import got AI-enriched, the manual
# upload didn't).
_FILL_FIELDS = (
    "summary", "keywords", "tags", "doc_type", "importance",
    "folder_id", "assignment_id", "page_count",
)

# Below this, two titles are treated as different documents even filed the
# same way -- two different worksheets named "Homework" is the main
# false-positive risk, so this stays high.
_TITLE_SIMILARITY_THRESHOLD = 0.82


def _normalize_title(title: str | None) -> str:
    return " ".join((title or "").lower().split())


def _title_similarity(a: str | None, b: str | None) -> float:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _looks_like_duplicate(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Similarity score (0..1) if `a`/`b` look like the same file added
    twice, else None."""
    if a.get("course_id") != b.get("course_id"):
        return None
    similarity = _title_similarity(a.get("title"), b.get("title"))
    if similarity < _TITLE_SIMILARITY_THRESHOLD:
        return None
    return similarity


def _fill_count(a: dict[str, Any]) -> int:
    return sum(1 for field in _FILL_FIELDS if a.get(field) not in (None, "", []))


def _pick_keeper(a: dict[str, Any], b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Which of the pair to suggest keeping: whichever is already fully
    indexed and has more filled-in detail, falling back to the earlier
    upload as a stable tiebreaker. Only a suggestion -- the student picks
    which side to actually keep."""
    ia, ib = bool(a.get("ingested")), bool(b.get("ingested"))
    if ia != ib:
        return (a, b) if ia else (b, a)
    fa, fb = _fill_count(a), _fill_count(b)
    if fa != fb:
        return (a, b) if fa > fb else (b, a)
    return (a, b) if (a.get("created_at") or "") <= (b.get("created_at") or "") else (b, a)


async def find_possible_duplicates(user_id: str) -> list[dict[str, Any]]:
    """Every pair of the student's own documents that look like the same
    file added twice, most-confident first, excluding any pair already
    dismissed as not-the-same-document."""
    documents = await supabase.select(
        "documents",
        columns="id,title,course_id,folder_id,assignment_id,doc_type,summary,keywords,tags,"
                "importance,page_count,size_bytes,ingested,created_at",
        filters={"user_id": eq(user_id)},
        limit=1000,
    ) or []
    dismissed = await supabase.select(
        "document_duplicate_dismissals",
        columns="document_id_a,document_id_b",
        filters={"user_id": eq(user_id)},
        limit=1000,
    ) or []
    dismissed_pairs = {(d["document_id_a"], d["document_id_b"]) for d in dismissed}

    candidates: list[dict[str, Any]] = []
    for a, b in combinations(documents, 2):
        if _pair_key(a["id"], b["id"]) in dismissed_pairs:
            continue
        score = _looks_like_duplicate(a, b)
        if score is None:
            continue
        keep, discard = _pick_keeper(a, b)
        candidates.append({
            "score": round(score, 3),
            "suggested_keep_id": keep["id"],
            "suggested_discard_id": discard["id"],
            "documents": [a, b],
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


async def _reassign_chunks(keep_id: str, discard_id: str) -> None:
    """Moves every chunk from the discarded document onto the kept one
    instead of just dropping them, so a merge never loses content the kept
    document's own text didn't cover -- e.g. the discarded upload has a page
    the kept one is missing. `document_chunks` has a unique
    (document_id, chunk_index) constraint, so the discarded chunks are
    renumbered to continue right after the kept document's own, one row at a
    time (PostgREST has no bulk "column = column + offset" update)."""
    existing = await supabase.select(
        "document_chunks", columns="chunk_index",
        filters={"document_id": eq(keep_id)}, order="chunk_index.desc", limit=1,
    ) or []
    offset = (existing[0]["chunk_index"] + 1) if existing else 0

    discard_chunks = await supabase.select(
        "document_chunks", columns="id,chunk_index",
        filters={"document_id": eq(discard_id)}, order="chunk_index.asc", limit=2000,
    ) or []
    for chunk in discard_chunks:
        await supabase.update(
            "document_chunks",
            {"document_id": keep_id, "chunk_index": offset + chunk["chunk_index"]},
            filters={"id": eq(chunk["id"])},
        )


async def _reassign_concepts(keep_id: str, discard_id: str) -> None:
    """Moves the discarded document's extracted concepts onto the kept one,
    skipping any concept the kept document is already tagged with --
    `document_concepts`' primary key is (document_id, concept_id), so a
    blind reassign would conflict on the overlap. Concepts left behind are
    cleaned up by the cascade delete when the discarded document is removed."""
    keep_concepts = await supabase.select(
        "document_concepts", columns="concept_id",
        filters={"document_id": eq(keep_id)}, limit=1000,
    ) or []
    keep_concept_ids = {c["concept_id"] for c in keep_concepts}

    discard_concepts = await supabase.select(
        "document_concepts", columns="concept_id",
        filters={"document_id": eq(discard_id)}, limit=1000,
    ) or []
    for c in discard_concepts:
        if c["concept_id"] in keep_concept_ids:
            continue
        await supabase.update(
            "document_concepts", {"document_id": keep_id},
            filters={"document_id": eq(discard_id), "concept_id": eq(c["concept_id"])},
        )


async def merge_documents(user_id: str, keep_id: str, discard_id: str) -> dict[str, Any]:
    """Merges `discard_id` into `keep_id`: moves the discarded document's
    chunks, extracted concepts, and flashcards onto the kept document, fills
    in any of the kept row's blank fields from the discarded one, then
    deletes the discarded row (queuing its file for delayed R2 removal, same
    as a normal document delete). Never destructive to indexed content --
    the whole point of a merge is that nothing either side already has gets
    lost, not that one side's work is thrown away."""
    if keep_id == discard_id:
        raise ValueError("Can't merge a document with itself.")
    rows = await supabase.select(
        "documents",
        filters={"user_id": eq(user_id), "id": f"in.({keep_id},{discard_id})"},
        limit=2,
    ) or []
    by_id = {r["id"]: r for r in rows}
    if keep_id not in by_id or discard_id not in by_id:
        raise LookupError("One or both documents were not found.")
    keep, discard = by_id[keep_id], by_id[discard_id]

    await _reassign_chunks(keep_id, discard_id)
    await _reassign_concepts(keep_id, discard_id)
    await supabase.update(
        "flashcards", {"document_id": keep_id},
        filters={"user_id": eq(user_id), "document_id": eq(discard_id)},
    )

    fill = {
        field: discard[field] for field in _FILL_FIELDS
        if keep.get(field) in (None, "", []) and discard.get(field) not in (None, "", [])
    }
    # A document that picked up any of the discarded document's content is
    # no longer fully represented by its own indexing pass alone.
    if not keep.get("ingested") and discard.get("ingested"):
        fill["ingested"] = True
    if fill:
        updated = await supabase.update(
            "documents", fill, filters={"user_id": eq(user_id), "id": eq(keep_id)},
        )
        keep = updated[0] if updated else keep

    # Best-effort: a schedule extraction ("at a glance") that pointed at the
    # discarded document's id so a future resync/dedupe check against it
    # doesn't silently no-op against a row that no longer exists. Not fatal
    # if this misses -- same best-effort standard as the rest of ingestion.
    try:
        pointing = await supabase.select(
            "calendar_events", columns="id,metadata",
            filters={
                "user_id": eq(user_id),
                "metadata->>source_document_id": eq(discard_id),
            },
            limit=200,
        ) or []
        for event in pointing:
            metadata = {**(event.get("metadata") or {}), "source_document_id": keep_id}
            await supabase.update(
                "calendar_events", {"metadata": metadata}, filters={"id": eq(event["id"])},
            )
    except Exception:
        pass

    storage_path = discard.get("storage_path")
    await supabase.delete("documents", filters={"user_id": eq(user_id), "id": eq(discard_id)})
    if storage_path:
        await storage_cleanup.queue_deletion(storage_path)

    return keep


async def dismiss_duplicate(user_id: str, document_id_a: str, document_id_b: str) -> None:
    """Remembers that this pair isn't actually the same document, so it
    stops being suggested. Stored with a canonical (min, max) id order (see
    `_pair_key`) so the same pair is one row no matter which order a later
    scan happens to compare the two documents in."""
    a, b = _pair_key(document_id_a, document_id_b)
    await supabase.insert(
        "document_duplicate_dismissals",
        {"user_id": user_id, "document_id_a": a, "document_id_b": b},
        upsert=True, on_conflict="user_id,document_id_a,document_id_b",
    )
