"""Grades a submitted practice quiz/test against `Tutor.quiz`'s own answer
key, records what went wrong into `mistakes` (migration 0003), and feeds
each question's outcome into the concept knowledge model's spaced-
repetition schedule (`app.services.knowledge_model.review`).

This is the missing middle between "Atlas generates a quiz" and "Atlas
learns what the student struggles with": before this, `mistakes` had no
producer (the table existed but nothing ever inserted into it) and
`knowledge_model.observe_grade` was never called from anywhere -- a quiz
would show its answer key and stop, no different from a static answer
sheet. This module is what actually closes that loop.

A batched LLM call grades every question at once when an LLM is configured
-- this handles a paraphrased short-answer response a strict string match
would wrongly mark incorrect, and classifies *why* a wrong answer was wrong
(conceptual/careless/procedural/knowledge_gap) so the recorded mistake is
useful, not just "wrong." Without an LLM configured, grading falls back to
plain string/choice matching with no mistake-type classification -- still
functional, just less forgiving and less specific.
"""
from __future__ import annotations

from typing import Any

from app.agents.archivist import Archivist
from app.config import settings
from app.core.supabase_client import eq, supabase
from app.llm import claude
from app.services import knowledge_model, practice

_MISTAKE_TYPES = {"conceptual", "careless", "procedural", "knowledge_gap"}

# Recall quality (0-5, see knowledge_model.compute_sm2) fed into the SM-2
# schedule for a wrong answer -- a careless slip means the student actually
# knows the material (barely nudge its review interval back), a conceptual/
# knowledge-gap miss means they don't (reset it hard), procedural sits in
# between. Mirrors observe_grade's percentage->quality mapping but applied
# per question instead of one blunt number stamped across every concept a
# session touched.
_WRONG_QUALITY = {"careless": 3, "procedural": 2, "conceptual": 1, "knowledge_gap": 1}
_DEFAULT_WRONG_QUALITY = 2


def _normalize(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _heuristic_grade(question: dict[str, Any], given: str | None) -> bool:
    """String/choice match used when no LLM is configured -- exact-ish only,
    no credit for a correctly-paraphrased short answer."""
    if not given:
        return False
    g = _normalize(given)
    answer = _normalize(question.get("answer"))
    if g == answer:
        return True
    choices = question.get("choices") or []
    if question.get("type") == "multiple_choice" and len(g) == 1:
        idx = "abcd".find(g)
        if 0 <= idx < len(choices) and _normalize(choices[idx]) == answer:
            return True
    return False


async def _llm_grade(
    questions: list[dict[str, Any]], answers: list[str | None]
) -> list[dict[str, Any]] | None:
    items = [
        {
            "i": i, "question": q.get("q"), "type": q.get("type"), "choices": q.get("choices"),
            "correct_answer": q.get("answer"),
            "student_answer": answers[i] if i < len(answers) else None,
        }
        for i, q in enumerate(questions)
    ]
    prompt = f"""\
Grade a student's practice quiz answers against the answer key. For each
question, judge correctness the way a fair teacher would -- a paraphrased
short answer that captures the right idea is correct, not just an exact
string match. A blank/missing student_answer is always incorrect.

When an answer is incorrect, classify why in one word:
- "conceptual": misunderstood the underlying idea
- "careless": knew it but slipped (arithmetic error, misread the question, wrong sign, etc.)
- "procedural": knew the idea but executed the steps/method wrong
- "knowledge_gap": doesn't seem to know this material at all

QUESTIONS: {items}

Return JSON: {{"results": [
  {{"i": <index>, "correct": true|false,
    "mistake_type": "conceptual"|"careless"|"procedural"|"knowledge_gap"|null,
    "correction": "one sentence: what was wrong and the right idea, or null if correct"}}
]}}"""
    try:
        data = await claude.complete_json(
            system="You are a fair, precise grading assistant.", prompt=prompt, max_tokens=2000,
        )
        results = data.get("results")
        return results if isinstance(results, list) else None
    except Exception:
        return None


async def _resolve_concept(user_id: str, name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    return await Archivist()._upsert_concept(user_id, {"name": name})


async def grade_submission(
    user_id: str, session_id: str, answers: list[str | None],
) -> dict[str, Any]:
    """Grades `session_id`'s questions against `answers` (index-aligned with
    the session's own `questions`), records mistakes and knowledge-model
    updates (unless the student has turned automatic mistake tracking off
    in Settings), saves the resulting score onto the session, and returns
    per-question feedback for the frontend to show in place of a plain
    reveal-answer."""
    session = await practice.get_session(user_id, session_id)
    if not session:
        raise LookupError("Practice session not found.")
    questions: list[dict[str, Any]] = session.get("questions") or []
    if not questions:
        raise ValueError("This practice session has no questions to grade.")

    profile_rows = await supabase.select("profiles", filters={"id": eq(user_id)}, limit=1)
    preferences = (profile_rows[0].get("preferences") if profile_rows else None) or {}
    tracking_enabled = preferences.get("mistake_tracking_enabled", True)

    graded = await _llm_grade(questions, answers) if settings.has_llm else None
    by_index = {r["i"]: r for r in graded} if graded else {}

    course_id = session.get("course_id")
    results: list[dict[str, Any]] = []
    correct_count = 0
    for i, q in enumerate(questions):
        given = answers[i] if i < len(answers) else None
        verdict = by_index.get(i)
        if verdict is not None:
            correct = bool(verdict.get("correct"))
            mistake_type = verdict.get("mistake_type") if verdict.get("mistake_type") in _MISTAKE_TYPES else None
            correction = verdict.get("correction")
        else:
            correct = _heuristic_grade(q, given)
            mistake_type = None
            correction = None if correct else q.get("explanation")

        if correct:
            correct_count += 1

        concept_id = await _resolve_concept(user_id, q.get("concept"))
        if tracking_enabled and not correct:
            await supabase.insert("mistakes", {
                "user_id": user_id, "course_id": course_id, "concept_id": concept_id,
                "description": q.get("q"), "mistake_type": mistake_type,
                "correction": correction or q.get("explanation"),
            })
        if concept_id:
            quality = 5 if correct else _WRONG_QUALITY.get(mistake_type, _DEFAULT_WRONG_QUALITY)
            try:
                await knowledge_model.review(user_id, concept_id, quality)
            except Exception:
                pass  # best-effort -- grading/mistake-recording already succeeded either way

        results.append({
            "i": i, "question": q.get("q"), "your_answer": given, "correct": correct,
            "correct_answer": q.get("answer"), "explanation": q.get("explanation"),
            "mistake_type": mistake_type, "correction": correction,
        })

    score = round(100 * correct_count / len(questions), 1)
    await practice.record_score(user_id, session_id, score)
    return {"session_id": session_id, "score": score, "results": results}
