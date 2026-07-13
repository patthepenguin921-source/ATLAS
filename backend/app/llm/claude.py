"""Claude — Atlas's primary reasoning engine.

Claude is NOT the memory. It receives relevant facts + semantic context
retrieved from Atlas's databases and reasons over them. Every agent grounds
its prompts in the student's actual academic history.
"""
from __future__ import annotations

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if not settings.has_claude:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def complete(
    *,
    system: str,
    messages: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.4,
    fast: bool = False,
) -> str:
    """Return Claude's text response for a grounded conversation."""
    client = _get_client()
    chosen = model or (settings.atlas_claude_fast_model if fast else settings.atlas_claude_model)
    resp = await client.messages.create(
        model=chosen,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


async def complete_json(
    *,
    system: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    fast: bool = False,
) -> Any:
    """Ask Claude for structured JSON and parse it robustly."""
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
