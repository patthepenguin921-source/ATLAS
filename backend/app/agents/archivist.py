"""Archivist — organizes every file, extracts metadata, links concepts."""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.core.supabase_client import eq, supabase
from app.embeddings.embedder import embed_text
from app.llm import claude


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
            "doc_type": data.get("doc_type", "other"),
        }
        title = (data.get("title") or "").strip()
        # Only override the stored title when the uploader didn't give a real
        # one (a filename-derived placeholder gets replaced by the AI title).
        if title and rename_untitled:
            update["title"] = title
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
        )
        return created[0]["id"] if created else None
