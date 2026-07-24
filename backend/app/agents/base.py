"""Base agent — a specialized intelligence grounded in shared memory.

All agents share the same memory (Atlas's databases). Each agent differs only
in persona and specialized behavior. Every response is grounded: the agent
retrieves the relevant slice of the student's academic history and reasons over
it, rather than answering from the conversation alone.
"""
from __future__ import annotations

from typing import Any

from app.agents.persona import ATLAS_SHARED_PRINCIPLES
from app.llm import claude
from app.services import memory, web_search

# `semantic_search` has no real relevance cutoff (default similarity_threshold
# is 0.0) — it always returns the *closest* chunks even when none actually
# answer the question, so "relevant_passages is non-empty" alone isn't a
# reliable signal that the student's documents had the answer. Require the
# single best match to clear this cosine-similarity bar before treating the
# question as "answered from your documents" instead of falling back to the web.
_RELEVANT_SIMILARITY_THRESHOLD = 0.5


class Agent:
    role: str = "general"
    name: str = "Atlas"
    persona: str = "You are Atlas, an academic operating system."

    def system_prompt(self, context_text: str) -> str:
        return (
            f"{self.persona}\n\n{ATLAS_SHARED_PRINCIPLES}\n\n"
            f"{context_text}\n\n"
            "Ground every statement in the context above. If the context lacks "
            "the answer, say so plainly rather than inventing facts. Anything "
            "under \"Found online\" did not come from the student's own "
            "documents or academic records — say so explicitly whenever you "
            "use it (e.g. \"I couldn't find this in your materials, but online "
            "sources say...\"). Never present a web result as if it came from "
            "the student's own documents, and never present document/record "
            "content as if it were a web result."
        )

    async def respond(
        self,
        user_id: str,
        user_message: str,
        *,
        history: list[dict[str, str]] | None = None,
        include_semantic: bool = True,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        ctx = await memory.build_context(
            user_id, user_message, include_semantic=include_semantic
        )
        # The student's own documents/records had nothing relevant — fall back
        # to the open web rather than answering from unlabeled general
        # knowledge. Kept as its own context block (never merged into
        # "document context") so the model can only ever cite it as web-sourced.
        passages = ctx.get("relevant_passages") or []
        best_similarity = max((p.get("similarity") or 0.0 for p in passages), default=0.0)
        web_results: list[dict] = []
        if include_semantic and best_similarity < _RELEVANT_SIMILARITY_THRESHOLD:
            web_results = await web_search.search(user_message)
        ctx["web_results"] = web_results
        context_text = memory.render_context(ctx)
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})
        text = await claude.complete(
            system=self.system_prompt(context_text),
            messages=messages,
            max_tokens=max_tokens,
        )
        return {
            "agent": self.role,
            "reply": text,
            "context_used": {
                "courses": len(ctx.get("courses", [])),
                "upcoming": len(ctx.get("upcoming", [])),
                "passages": len(ctx.get("relevant_passages", [])),
                "web_results": len(ctx.get("web_results", [])),
            },
        }
