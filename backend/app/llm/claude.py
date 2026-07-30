"""Reasoning engine — Atlas's grounded LLM layer.

Pluggable provider so the reasoning engine does not depend on any single
vendor:

    groq       — free tier (Llama 3.3 70B / 3.1 8B), the default. $0 forever.
    gemini     — also free (Google AI Studio), materially higher free-tier
                 rate limits than Groq's and generally stronger reasoning
                 than Llama 3.3 70B. Set ATLAS_LLM_PROVIDER=gemini and
                 GEMINI_API_KEY to use it as the primary provider.
    anthropic  — Claude, optional *paid* upgrade for the highest-quality
                 reasoning (needs ANTHROPIC_API_KEY). Set
                 ATLAS_LLM_PROVIDER=anthropic.

`ATLAS_LLM_FALLBACK_PROVIDER` (default "gemini") adds resilience for free:
if the primary provider returns a 429 (rate limited), the same call is
retried once against the fallback provider instead of failing the chat
turn -- so with the defaults, a request only fails if *both* Groq's and
Gemini's free tiers are exhausted at the same moment.

The engine is NOT the memory. It receives relevant facts + semantic context
retrieved from Atlas's databases and reasons over them. Every agent grounds
its prompts in the student's actual academic history.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

import anthropic
import httpx
from anthropic import AsyncAnthropic

from app.config import settings

_anthropic_client: AsyncAnthropic | None = None


class LLMError(RuntimeError):
    """An upstream LLM provider (Groq or Anthropic) call failed -- most
    commonly a 429 from Groq's free tier or a Claude rate/overload error.
    Raised instead of letting the provider SDK/httpx exception propagate
    unhandled, so `app.main`'s exception handler (mirroring `SupabaseError`/
    `R2Error`) can turn it into a real JSON response that still carries CORS
    headers. An exception with no registered handler is caught by Starlette's
    outermost ServerErrorMiddleware, which sits *outside* CORSMiddleware --
    the resulting 500 has no Access-Control-Allow-Origin header, so the
    browser reports it to the caller as a generic "Failed to fetch" network
    error instead of a readable rate-limit message."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"LLM provider error {status}: {detail}")
        self.status = status
        self.detail = detail


def _get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


def _fallback_provider(primary: str) -> str | None:
    """The provider to retry a rate-limited call against, or None if
    fallback is disabled, would just retry the same provider, or has no
    credentials configured (in which case failing fast with the original
    error is more useful than a second, guaranteed-to-fail call)."""
    fallback = settings.atlas_llm_fallback_provider
    if not fallback or fallback == primary:
        return None
    return fallback if settings._llm_credential(fallback) else None


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
    primary = settings.atlas_llm_provider
    fn = _COMPLETE_PROVIDERS.get(primary, _complete_groq)
    try:
        return await fn(
            system=system, messages=messages, model=model,
            max_tokens=max_tokens, temperature=temperature, fast=fast,
        )
    except LLMError as exc:
        fallback = _fallback_provider(primary) if exc.status == 429 else None
        if not fallback:
            raise
        # `model` isn't retried across providers -- a model name from one
        # vendor means nothing to another -- so the fallback call always
        # uses its own configured default instead.
        return await _COMPLETE_PROVIDERS[fallback](
            system=system, messages=messages, model=None,
            max_tokens=max_tokens, temperature=temperature, fast=fast,
        )


async def _complete_anthropic(
    *, system: str, messages: list[dict[str, Any]], model: str | None,
    max_tokens: int, temperature: float, fast: bool,
) -> str:
    client = _get_anthropic_client()
    chosen = model or (settings.atlas_claude_fast_model if fast else settings.atlas_claude_model)
    try:
        resp = await client.messages.create(
            model=chosen,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except anthropic.APIStatusError as exc:
        raise LLMError(exc.status_code, str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(503, str(exc)) from exc
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
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(r.status_code, r.text) from exc
        return r.json()["choices"][0]["message"]["content"].strip()


_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
    ]


def _gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if "text" in p).strip()


async def _complete_gemini(
    *, system: str, messages: list[dict[str, Any]], model: str | None,
    max_tokens: int, temperature: float, fast: bool,
) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    chosen = model or (settings.atlas_gemini_fast_model if fast else settings.atlas_gemini_model)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{_GEMINI_BASE}/{chosen}:generateContent",
            params={"key": settings.gemini_api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": _to_gemini_contents(messages),
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
            },
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(r.status_code, r.text) from exc
        return _gemini_text(r.json())


_COMPLETE_PROVIDERS: dict[str, Callable[..., Awaitable[str]]] = {
    "groq": _complete_groq,
    "gemini": _complete_gemini,
    "anthropic": _complete_anthropic,
}


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
    word that something was done).

    On a 429 from the primary provider, retries the whole call once against
    the configured fallback provider (see `_fallback_provider`) starting
    from the original `messages` -- any tool call the primary provider
    already executed before hitting the limit stays executed (side effects
    aren't undone), so the fallback attempt can in rare cases repeat one.
    That's an accepted tradeoff for keeping this a plain retry rather than
    resumable cross-provider tool-call state; a 429 on the very first model
    call of the turn -- before any tool has run -- is the overwhelmingly
    common case in practice."""
    primary = settings.atlas_llm_provider
    fn = _AGENTIC_PROVIDERS.get(primary, _agentic_groq)
    try:
        return await fn(
            system=system, messages=messages, tools=tools, execute_tool=execute_tool,
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
    except LLMError as exc:
        fallback = _fallback_provider(primary) if exc.status == 429 else None
        if not fallback:
            raise
        return await _AGENTIC_PROVIDERS[fallback](
            system=system, messages=messages, tools=tools, execute_tool=execute_tool,
            model=None, max_tokens=max_tokens, temperature=temperature,
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
        try:
            resp = await client.messages.create(
                model=chosen, system=system, messages=convo,
                tools=anthropic_tools, max_tokens=max_tokens, temperature=temperature,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(exc.status_code, str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(503, str(exc)) from exc
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
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(r.status_code, r.text) from exc
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


async def _agentic_gemini(
    *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    execute_tool: ToolExecutor, model: str | None, max_tokens: int, temperature: float,
) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    chosen = model or settings.atlas_gemini_model
    gemini_tools = [{"functionDeclarations": [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in tools
    ]}]
    convo = _to_gemini_contents(messages)
    calls_made: list[dict[str, Any]] = []
    text = ""
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(_MAX_TOOL_ROUNDS):
            r = await client.post(
                f"{_GEMINI_BASE}/{chosen}:generateContent",
                params={"key": settings.gemini_api_key},
                json={
                    "system_instruction": {"parts": [{"text": system}]},
                    "contents": convo,
                    "tools": gemini_tools,
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
                },
            )
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(r.status_code, r.text) from exc
            candidates = r.json().get("candidates") or []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
            function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not function_calls:
                return {"text": text, "tool_calls": calls_made}
            convo.append({"role": "model", "parts": parts})
            response_parts = []
            for call in function_calls:
                name, arguments = call["name"], call.get("args") or {}
                result = await execute_tool(name, arguments)
                calls_made.append({"name": name, "arguments": arguments, "result": result})
                response_parts.append({"functionResponse": {"name": name, "response": result}})
            convo.append({"role": "user", "parts": response_parts})
    return {"text": text, "tool_calls": calls_made}


_AGENTIC_PROVIDERS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "groq": _agentic_groq,
    "gemini": _agentic_gemini,
    "anthropic": _agentic_anthropic,
}
