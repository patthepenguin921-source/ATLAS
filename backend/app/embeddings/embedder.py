"""Embeddings — Atlas's semantic memory encoder.

Pluggable provider so semantic memory does not depend on any single vendor:

    voyage  — Anthropic-recommended embeddings (needs VOYAGE_API_KEY)
    openai  — text-embedding-3 family (needs OPENAI_API_KEY)
    local   — deterministic hashing embedding, NO semantic quality, but lets
              the whole app run keyless for development/CI.

All providers return vectors of length ``settings.embeddings_dim`` (1024),
matching the ``vector(1024)`` columns in Postgres.
"""
from __future__ import annotations

import hashlib
import math

import httpx

from app.config import settings

DIM = settings.embeddings_dim


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    provider = settings.embeddings_provider.lower()
    if provider == "voyage" and settings.voyage_api_key:
        return await _voyage(texts)
    if provider == "openai" and settings.openai_api_key:
        return await _openai(texts)
    return [_local(t) for t in texts]


async def embed_text(text: str) -> list[float]:
    return (await embed_texts([text]))[0]


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
async def _voyage(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"input": texts, "model": settings.embeddings_model, "output_dimension": DIM},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]


async def _openai(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"input": texts, "model": settings.embeddings_model or "text-embedding-3-small",
                  "dimensions": DIM},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]


def _local(text: str) -> list[float]:
    """Deterministic bag-of-words hashing embedding (dev fallback).

    Not semantically meaningful, but stable and L2-normalized so cosine
    similarity behaves and the pipeline is fully exercisable without a key.
    """
    vec = [0.0] * DIM
    for token in _tokenize(text):
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        idx = h % DIM
        sign = 1.0 if (h >> 7) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _tokenize(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]
