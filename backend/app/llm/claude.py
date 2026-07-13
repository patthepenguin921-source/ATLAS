"""Reasoning engine — Atlas's grounded LLM layer.

Pluggable provider so the reasoning engine does not depend on any single
vendor:

    groq       — free tier (Llama 3.3 70B / 3.1 8B), the default. $0 forever.
    anthropic  — Claude, optional upgrade for higher-quality reasoning
                 (needs ANTHROPIC_API_KEY). Set ATLAS_LLM_PROVIDER=anthropic.

The engine is NOT the memory. It receives relevant facts + semantic context
retrieved from Atlas's databases and reasons over them. Every agent grounds
its prompts in the student's actual academic history.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from app.config import settings

_anthropic_client: AsyncAnthropic | None = None


def _get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


async def complete(
    *,
    system: str,
    messages: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.4,
    fast: bool = False,
) -> str:
    """Return the reasoning engine's text response for a grounded conversation."""
    if settings.atlas_llm_provider == "anthropic":
        return await _complete_anthropic(
            system=system, messages=messages, model=model,
            max_tokens=max_tokens, temperature=temperature, fast=fast,
        )
    return await _complete_groq(
        system=system, messages=messages, model=model,
        max_tokens=max_tokens, temperature=temperature, fast=fast,
    )


async def _complete_anthropic(
    *, system: str, messages: list[dict[str, Any]], model: str | None,
    max_tokens: int, temperature: float, fast: bool,
) -> str:
    client = _get_anthropic_client()
    chosen = model or (settings.atlas_claude_fast_model if fast else settings.atlas_claude_model)
    resp = await client.messages.create(
        model=chosen,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


async def _complete_groq(
    *, system: str, messages: list[dict[str, Any]], model: str | None,
    max_tokens: int, temperature: float, fast: bool,
) -> str:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    chosen = model or (settings.atlas_groq_fast_model if fast else settings.atlas_groq_model)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": chosen,
                "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def complete_json(
    *,
    system: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    fast: bool = False,
) -> Any:
    """Ask the reasoning engine for structured JSON and parse it robustly."""
    system_json = (
        system
        + "\n\nRespond with a single valid JSON value and nothing else. "
        "Do not wrap it in markdown fences."
    )
    text = await complete(
        system=system_json,
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        fast=fast,
    )
    return _extract_json(text)


def _extract_json(text: str) -> Any:
    text = text.strip()
    # strip ```json fences if present
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last-ditch: grab the outermost {...} or [...]
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise
