"""Base agent — a specialized intelligence grounded in shared memory.

All agents share the same memory (Atlas's databases). Each agent differs only
in persona and specialized behavior. Every response is grounded: the agent
retrieves the relevant slice of the student's academic history and reasons over
it, rather than answering from the conversation alone.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agents.persona import ATLAS_SHARED_PRINCIPLES
from app.llm import claude
from app.services import memory, web_search


async def _no_web_search() -> tuple[list[dict], str | None]:
    return [], None


class Agent:
    role: str = "general"
    name: str = "Atlas"
    persona: str = "You are Atlas, an academic operating system."

    def system_prompt(self, context_text: str) -> str:
        return (
            f"{self.persona}\n\n{ATLAS_SHARED_PRINCIPLES}\n\n"
            f"{context_text}\n\n"
            "Ground every statement in the context above. Passages under "
            "\"Relevant passages from your documents\" being present doesn't "
            "mean they answer the question — they're just the closest matches "
            "found; judge for yourself whether they actually contain the "
            "answer. If they don't, use \"Found online\" results instead if "
            "present. Anything under \"Found online\" did not come from the "
            "student's own documents or academic records — flag it once, "
            "briefly, when you use it (e.g. \"(found online, not in your "
            "materials)\"). Never present a web result as if it came from the "
            "student's own documents, and never present document/record "
            "content as if it were a web result. If neither source has the "
            "answer, say so in one short sentence and stop — don't "
            "editorialize about what else the student might have meant."
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
        # Run document retrieval and a web search side by side rather than
        # deciding from document-passage similarity alone whether the web is
        # needed: embedding similarity reflects topical closeness, not whether
        # a passage actually contains the answer (e.g. "AP Research" chunks
        # score as relevant to "what percent got a 5" even with no score data
        # in them), so gating the web search on a similarity threshold missed
        # exactly this case. Both sources go into context, clearly separated,
        # and the model — not a similarity score — judges which one (if
        # either) actually answers the question.
        ctx, (web_results, web_search_error) = await asyncio.gather(
            memory.build_context(user_id, user_message, include_semantic=include_semantic),
            web_search.search(user_message) if include_semantic else _no_web_search(),
        )
        ctx["web_results"] = web_results
        ctx["web_search_error"] = web_search_error
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
                "web_search_error": ctx.get("web_search_error"),
            },
        }
