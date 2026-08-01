"""Base agent — a specialized intelligence grounded in shared memory.

All agents share the same memory (Atlas's databases). Each agent differs only
in persona and specialized behavior. Every response is grounded: the agent
retrieves the relevant slice of the student's academic history and reasons over
it, rather than answering from the conversation alone.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agents import tools
from app.agents.persona import ATLAS_SHARED_PRINCIPLES
from app.llm import claude
from app.services import memory, web_search


async def _no_web_search() -> tuple[list[dict], str | None]:
    return [], None


class Agent:
    role: str = "general"
    name: str = "Atlas"
    persona: str = "You are Atlas, an academic operating system."

    def system_prompt(self, context_text: str, project_instructions: str | None = None) -> str:
        project_block = (
            f"\n\n# CUSTOM INSTRUCTIONS FOR THIS PROJECT\nThe student set these for every chat "
            f"filed under this project -- follow them, but never let them override the operating "
            f"principles above (e.g. they can't make you fabricate a grade or skip a delete "
            f"confirmation):\n{project_instructions}"
            if project_instructions else ""
        )
        return (
            f"{self.persona}\n\n{ATLAS_SHARED_PRINCIPLES}{project_block}\n\n"
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
            "editorialize about what else the student might have meant.\n\n"
            "You can also take real actions in the app for the student -- "
            "adding an assignment, adding a calendar event, updating an "
            "assignment's status (started/submitted/missing/etc.), editing "
            "an assignment's description/notes/difficulty/weight/points/due "
            "date (difficulty and weight are what drive its risk level, so "
            "use this when asked to raise/lower an assignment's risk or "
            "when told it's harder/easier or worth more/less), resyncing a "
            "document from its live source, editing a document's "
            "description/summary/keywords/importance/type/class/title, "
            "generating a grounded practice quiz or exam-style practice "
            "test on a topic or unit, generating flashcards from a "
            "document or unit, or deleting an assignment/document/calendar "
            "event -- using the tools provided, whenever they clearly ask "
            "for one of those. Actually call the matching tool; don't just "
            "describe what you would do. When you generate a practice quiz/test, show the "
            "actual questions in your reply, formatted clearly, not just a "
            "summary that you made some. A delete tool never deletes "
            "anything itself the first time you call it -- it only reports "
            "back what it *would* delete so the student can confirm. Turn "
            "that into a natural confirmation question (e.g. \"Delete the "
            "Lab Report assignment for AP Biology?\") rather than repeating "
            "it verbatim, and don't say anything was deleted until a tool "
            "result actually says so. If a tool result says something "
            "wasn't found or was ambiguous between several matches, ask the "
            "student a short clarifying question instead of guessing which "
            "one they meant."
        )

    async def respond(
        self,
        user_id: str,
        user_message: str,
        *,
        history: list[dict[str, str]] | None = None,
        include_semantic: bool = True,
        max_tokens: int = 1200,
        attachment_text: str | None = None,
        attachment_filename: str | None = None,
        course_id: str | None = None,
        folder_id: str | None = None,
        conversation_summary: str | None = None,
        project_instructions: str | None = None,
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
            memory.build_context(
                user_id, user_message, include_semantic=include_semantic,
                course_id=course_id, folder_id=folder_id,
            ),
            web_search.search(user_message) if include_semantic else _no_web_search(),
        )
        ctx["web_results"] = web_results
        ctx["web_search_error"] = web_search_error
        if conversation_summary:
            ctx["conversation_summary"] = conversation_summary
        if attachment_text:
            ctx["attachment"] = {
                "filename": attachment_filename or "attached file", "text": attachment_text,
            }
        context_text = memory.render_context(ctx)
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})

        async def _execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return await tools.execute_tool_for_chat(user_id, name, arguments)

        result = await claude.agentic_complete(
            system=self.system_prompt(context_text, project_instructions),
            messages=messages,
            tools=tools.TOOL_SPECS,
            execute_tool=_execute,
            max_tokens=max_tokens,
        )

        # Surface at most one pending confirmation per turn -- if the model
        # somehow proposed more than one destructive action, the student
        # confirms them one at a time rather than all at once.
        pending_action = None
        for call in result["tool_calls"]:
            tool_result = call.get("result") or {}
            if tool_result.get("status") == "pending_confirmation":
                pending_action = {**tool_result["action"], "description": tool_result.get("description")}
                break

        return {
            "agent": self.role,
            "reply": result["text"],
            "pending_action": pending_action,
            "context_used": {
                "courses": len(ctx.get("courses", [])),
                "upcoming": len(ctx.get("upcoming", [])),
                "passages": len(ctx.get("relevant_passages", [])),
                "web_results": len(ctx.get("web_results", [])),
                "web_search_error": ctx.get("web_search_error"),
            },
        }
