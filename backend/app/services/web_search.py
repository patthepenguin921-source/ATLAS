"""Web search fallback — used only when the student's own data has nothing.

Kept as a separate, clearly-labeled source so Atlas never blurs "this is in
your documents/records" with "this is from the open internet."
"""
from __future__ import annotations

import httpx

from app.config import settings

_TAVILY_URL = "https://api.tavily.com/search"


async def search(query: str, *, max_results: int = 5) -> list[dict]:
    """Best-effort web search. Returns [] if unconfigured or on any failure."""
    if not settings.has_web_search:
        return []
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
    except Exception:
        return []
    return [
        {"title": item.get("title"), "url": item.get("url"), "content": item.get("content")}
        for item in data.get("results", [])[:max_results]
    ]
