"""Archivist — organizes every file, extracts metadata, links concepts."""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.config import settings
from app.core.supabase_client import eq, supabase
from app.embeddings.embedder import embed_text
from app.llm import claude
from app.services.folders import find_or_create_folder, top_level_folders


class Archivist(Agent):
    role = "archivist"
    name = "Archivist"
    persona = (
        "You are the Archivist agent of Atlas. You organize uploaded documents, "
        "extract precise metadata, and connect them into the knowledge graph so "
        "nothing is ever lost or has to be searched for twice."
    )

    async def enrich(
        self, user_id: str, document_id: str, text: str, *, rename_untitled: bool = False
    ) -> dict[str, Any]:
        """Summarize a document, extract keywords + concepts, and link concepts.

        When ``rename_untitled`` is set, a generated title replaces the stored
        one (used when the uploader relied on a filename-derived placeholder).
        """
        excerpt = text[:12000]
        prompt = f"""\
Analyze this document and return JSON:
{{
  "title": "a concise, human 3-8 word title describing what this document is (e.g. 'AP Bio Ch.4 Photosynthesis Notes', 'Algebra II Unit 3 Test Review')",
  "summary": "3-5 sentence summary",
  "keywords": ["..."],
  "doc_type": "pdf|powerpoint|notes|announcement|study_guide|essay|practice_problems|rubric|personal_note|email|image|other",
  "importance": "how much this document matters for the student to study/keep track of — 'high' for things like a syllabus, study guide, rubric, or exam review; 'low' for routine/low-stakes items like a single class announcement or a slide deck that just repeats the textbook; 'normal' for everything in between",
  "concepts": [
    {{"name": "canonical concept name", "description": "one line", "subject": "..."}}
  ]
}}

DOCUMENT:
{excerpt}"""
        data = await claude.complete_json(
            system=self.persona, prompt=prompt, max_tokens=1500, fast=True
        )

        update: dict[str, Any] = {
            "summary": data.get("summary"),
            "keywords": data.get("keywords", []),
        }
        # Used below to avoid clobbering a student's own manual choices, or a
        # `glance` tag this pipeline already set with certainty (see
        # `schedule_extraction._tag_as_glance`) — fetched once upfront rather
        # than as two separate round trips.
        existing = await supabase.select(
            "documents", columns="importance_source,doc_type_source,folder_source,course_id",
            filters={"id": eq(document_id)}, limit=1,
        )
        existing_row = existing[0] if existing else {}

        # Only ever guess `doc_type` here when it's still AI-sourced or has
        # never been set — never overrides a manual re-tag or a `system`-set
        # `glance` tag.
        if existing_row.get("doc_type_source") not in ("manual", "system"):
            update["doc_type"] = data.get("doc_type", "other")
            update["doc_type_source"] = "ai"
        title = (data.get("title") or "").strip()
        # Only override the stored title when the uploader didn't give a real
        # one (a filename-derived placeholder gets replaced by the AI title).
        if title and rename_untitled:
            update["title"] = title

        importance = data.get("importance")
        if importance in ("low", "normal", "high"):
            # Never override a student's own manual rating (see
            # `update_document`'s `importance_source` handling) — only ever
            # set it here when it's still AI-sourced or has never been set.
            if existing_row.get("importance_source") != "manual":
                update["importance"] = importance
                update["importance_source"] = "ai"

        # Auto-file into a topic/unit folder within the document's class (or
        # within "General" for a document with no class) — never when a
        # student already moved it themselves (see `folder_source`, same
        # override-survives-re-enrichment idiom as doc_type/importance
        # above). Reuses an existing folder by name when one fits; only
        # invents a new one when nothing does — see `classify_folder`.
        if existing_row.get("folder_source") != "manual":
            course_id = existing_row.get("course_id")
            candidates, parent_folder_id = await top_level_folders(user_id, course_id=course_id)
            try:
                guess = await self.classify_folder(excerpt[:6000], candidates)
            except Exception:
                guess = {"folder_name": None}
            folder_name = guess.get("folder_name")
            if folder_name:
                folder_id = await find_or_create_folder(
                    user_id, folder_name, course_id=course_id, parent_folder_id=parent_folder_id,
                )
                if folder_id:
                    update["folder_id"] = folder_id
                    update["folder_source"] = "ai"

        await supabase.update(
            "documents", update, filters={"id": eq(document_id)}
        )

        linked = []
        for concept in data.get("concepts", [])[:12]:
            cid = await self._upsert_concept(user_id, concept)
            if cid:
                await supabase.insert(
                    "document_concepts",
                    {"document_id": document_id, "concept_id": cid, "user_id": user_id},
                    upsert=True,
                )
                linked.append(concept["name"])
        return {"title": update.get("title"), "summary": data.get("summary"),
                "keywords": data.get("keywords", []), "concepts_linked": linked}

    async def enrich_or_fallback(
        self, user_id: str, document_id: str, text: str, *,
        rename_untitled: bool = False, fallback_title: str | None = None,
    ) -> None:
        """`enrich`, but guarantees the document ends up with *some* summary
        rather than staying blank forever. Runs the real AI enrichment when
        there's actual text and an LLM configured; otherwise -- or if that
        call itself fails -- falls back to a plain, literal one-liner (just
        naming what the document is) so even a no-text stub (a scanned image
        OCR couldn't read, a link Atlas hasn't downloaded yet, ...) is never
        left without a summary a student can see at a glance."""
        if settings.has_llm and text.strip():
            try:
                await self.enrich(user_id, document_id, text, rename_untitled=rename_untitled)
                return
            except Exception:  # noqa: BLE001
                pass
        rows = await supabase.select(
            "documents", columns="summary,title,doc_type",
            filters={"id": eq(document_id)}, limit=1,
        )
        if rows and rows[0].get("summary"):
            return
        title = (rows[0].get("title") if rows else None) or fallback_title or "Untitled document"
        doc_type = (rows[0].get("doc_type") if rows else None) or "other"
        label = doc_type.replace("_", " ") if doc_type != "other" else "document"
        await supabase.update(
            "documents", {"summary": f'A {label} titled "{title}".'},
            filters={"id": eq(document_id)},
        )

    async def classify_course(self, text: str, courses: list[dict[str, Any]]) -> dict[str, Any]:
        """Guess which of the student's existing courses a document belongs to.

        Always returns a course_id (best guess), even at low confidence — the
        caller marks low-confidence guesses for the student to review rather
        than leaving the document unfiled.
        """
        if not courses:
            return {"course_id": None, "confidence": 0.0}
        excerpt = text[:6000]
        options = [{"id": c["id"], "name": c.get("name"), "subject": c.get("subject")}
                   for c in courses]
        prompt = f"""\
A student dropped a file without saying which class it belongs to. Given the
document excerpt and the student's list of classes, pick the single best
matching class and how confident you are.

Return JSON: {{"course_id": "<id from the list>", "confidence": 0.0-1.0}}

CLASSES:
{options}

DOCUMENT EXCERPT:
{excerpt}"""
        data = await claude.complete_json(
            system=self.persona, prompt=prompt, max_tokens=200, fast=True
        )
        valid_ids = {c["id"] for c in courses}
        course_id = data.get("course_id")
        if course_id not in valid_ids:
            course_id = courses[0]["id"]
            confidence = 0.0
        else:
            try:
                confidence = float(data.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
        return {"course_id": course_id, "confidence": max(0.0, min(1.0, confidence))}

    async def classify_folder(
        self, excerpt: str, folders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Suggest a topic/unit folder for a document, within its class (or
        General, for a document with no class) — reusing an existing folder
        by name when one fits, inventing a short new one otherwise. Unlike
        `classify_course`, this never forces a guess: a document with no
        clear topic just stays at the class's top level (`folder_name: null`)
        rather than being crammed into an unrelated folder.
        """
        names = [f["name"] for f in folders]
        prompt = f"""\
A student's document needs a topic/unit folder within one of their classes
(or a general "not tied to a class" area). Suggest ONE short folder name for
it — reuse one of the EXISTING FOLDERS below if it clearly fits, or invent a
short new topic/unit name (e.g. "Unit 3 - Cell Biology", "Homework", "Study
guides", "Labs") if none of them do. If the document doesn't clearly belong
to any particular topic, return null instead of forcing a fit.

Return JSON: {{"folder_name": "<name>" | null}}

EXISTING FOLDERS: {names}

DOCUMENT EXCERPT:
{excerpt}"""
        data = await claude.complete_json(
            system=self.persona, prompt=prompt, max_tokens=150, fast=True
        )
        name = (data.get("folder_name") or "").strip()
        return {"folder_name": name or None}

    async def _upsert_concept(self, user_id: str, concept: dict[str, Any]) -> str | None:
        name = (concept.get("name") or "").strip()
        if not name:
            return None
        existing = await supabase.select(
            "concepts", columns="id",
            filters={"user_id": eq(user_id), "name": eq(name)}, limit=1,
        )
        if existing:
            return existing[0]["id"]
        try:
            embedding = await embed_text(f"{name}. {concept.get('description','')}")
        except Exception:
            embedding = None
        created = await supabase.insert(
            "concepts",
            {
                "user_id": user_id, "name": name,
                "description": concept.get("description"),
                "subject": concept.get("subject"),
                "embedding": embedding,
            },
            upsert=True,
            on_conflict="user_id,name",
        )
        return created[0]["id"] if created else None
