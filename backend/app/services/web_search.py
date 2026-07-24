"""Web search fallback — used only when the student's own data has nothing.

Kept as a separate, clearly-labeled source so Atlas never blurs "this is in
your documents/records" with "this is from the open internet."
"""
from __future__ import annotations

import httpx

from app.config import settings

_TAVILY_URL = "https://api.tavily.com/search"


async def search(query: str, *, max_results: int = 5) -> tuple[list[dict], str | None]:
    """Best-effort web search.

    Returns ([], reason) if unconfigured or on any failure — the reason string
    is surfaced into context (see memory.build_context's "web_search_error")
    instead of being silently swallowed, so a bad key or a blocked outbound
    request shows up instead of just looking like "no fallback happened."
    """
    if not settings.has_web_search:
        return [], "no TAVILY_API_KEY configured"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                _TAVILY_URL,
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return [], str(e)
    results = [
        {"title": item.get("title"), "url": item.get("url"), "content": item.get("content")}
        for item in data.get("results", [])[:max_results]
    ]
    return results, None
