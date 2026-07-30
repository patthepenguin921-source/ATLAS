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
from typing import Any, Awaitable, Callable

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


# --------------------------------------------------------------------------
# Tool use (function calling) -- lets an agent actually perform an action
# ("add this as an assignment", "resync that document") instead of only
# ever replying with text. Provider-agnostic: a caller describes its tools
# once in a plain JSON-Schema shape, and this module translates to whichever
# provider is configured (Anthropic's `input_schema` tool blocks vs. Groq's
# OpenAI-style `tools`/`tool_calls`) and drives the whole
# call -> execute -> feed-result-back -> final-reply loop itself, so a
# caller (see app.agents.tools) never has to know which provider is live.
# --------------------------------------------------------------------------
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_MAX_TOOL_ROUNDS = 4  # safety valve against a model looping on tool calls forever


async def agentic_complete(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Runs a full tool-use turn: asks the model, executes whatever tool
    calls it makes (via `execute_tool`, which actually performs the action
    and returns a JSON-serializable result), feeds each result back, and
    repeats until the model settles on a plain-text reply (or
    `_MAX_TOOL_ROUNDS` is hit, in which case whatever text is on hand so far
    is returned rather than looping forever).

    `tools` is a plain, provider-neutral list of
    ``{"name", "description", "parameters"}`` (parameters = a JSON Schema
    object) -- translated to each provider's own tool-calling shape here so
    callers never need to care which one is configured.

    Returns ``{"text": str, "tool_calls": [{"name", "arguments", "result"}]}``
    -- the latter lets the caller react to what actually happened (e.g. a
    destructive action `execute_tool` declined to actually perform, instead
    returning a ``{"status": "pending_confirmation", ...}`` result for the
    caller to surface as a real confirm step, not just take the model's own
    word that something was done)."""
    if settings.atlas_llm_provider == "anthropic":
        return await _agentic_anthropic(
            system=system, messages=messages, tools=tools, execute_tool=execute_tool,
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
    return await _agentic_groq(
        system=system, messages=messages, tools=tools, execute_tool=execute_tool,
        model=model, max_tokens=max_tokens, temperature=temperature,
    )


async def _agentic_anthropic(
    *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    execute_tool: ToolExecutor, model: str | None, max_tokens: int, temperature: float,
) -> dict[str, Any]:
    client = _get_anthropic_client()
    chosen = model or settings.atlas_claude_model
    anthropic_tools = [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in tools
    ]
    convo: list[Any] = list(messages)
    calls_made: list[dict[str, Any]] = []
    text = ""
    for _ in range(_MAX_TOOL_ROUNDS):
        resp = await client.messages.create(
            model=chosen, system=system, messages=convo,
            tools=anthropic_tools, max_tokens=max_tokens, temperature=temperature,
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return {"text": text, "tool_calls": calls_made}
        convo.append({"role": "assistant", "content": resp.content})
        result_blocks = []
        for block in tool_uses:
            result = await execute_tool(block.name, block.input or {})
            calls_made.append({"name": block.name, "arguments": block.input or {}, "result": result})
            result_blocks.append({
                "type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result),
            })
        convo.append({"role": "user", "content": result_blocks})
    return {"text": text, "tool_calls": calls_made}


async def _agentic_groq(
    *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    execute_tool: ToolExecutor, model: str | None, max_tokens: int, temperature: float,
) -> dict[str, Any]:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    chosen = model or settings.atlas_groq_model
    groq_tools = [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in tools
    ]
    convo: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
    calls_made: list[dict[str, Any]] = []
    text = ""
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(_MAX_TOOL_ROUNDS):
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": chosen, "messages": convo, "tools": groq_tools,
                    "max_tokens": max_tokens, "temperature": temperature,
                },
            )
            r.raise_for_status()
            message = r.json()["choices"][0]["message"]
            text = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return {"text": text, "tool_calls": calls_made}
            convo.append({
                "role": "assistant", "content": message.get("content"), "tool_calls": tool_calls,
            })
            for call in tool_calls:
                fn = call["function"]
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await execute_tool(fn["name"], arguments)
                calls_made.append({"name": fn["name"], "arguments": arguments, "result": result})
                convo.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    return {"text": text, "tool_calls": calls_made}
