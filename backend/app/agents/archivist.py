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

    async def enrich(self, user_id: str, document_id: str, text: str) -> dict[str, Any]:
        """Summarize a document, extract keywords + concepts, and link concepts."""
        excerpt = text[:12000]
        prompt = f"""\
Analyze this document and return JSON:
{{
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

        await supabase.update(
            "documents",
            {
                "summary": data.get("summary"),
                "keywords": data.get("keywords", []),
                "doc_type": data.get("doc_type", "other"),
            },
            filters={"id": eq(document_id)},
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
        return {"summary": data.get("summary"), "keywords": data.get("keywords", []),
                "concepts_linked": linked}

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
