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
from app.services import memory


class Agent:
    role: str = "general"
    name: str = "Atlas"
    persona: str = "You are Atlas, an academic operating system."

    def system_prompt(self, context_text: str) -> str:
        return (
            f"{self.persona}\n\n{ATLAS_SHARED_PRINCIPLES}\n\n"
            f"{context_text}\n\n"
            "Ground every statement in the context above. If the context lacks "
            "the answer, say so plainly rather than inventing facts."
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
            },
        }
