"""Schoology provider (scaffold).

Schoology exposes a REST API with OAuth 1.0a two-legged auth. To implement:
  1. Obtain a district/consumer key+secret (or user OAuth).
  2. GET /users/{id}/sections            -> courses
  3. GET /sections/{id}/assignments      -> assignments
  4. GET /sections/{id}/grades           -> grades
  5. GET /sections/{id}/updates          -> announcements
Normalize each into Atlas's schema via the helpers in `base.py`, then ingest
any attached files through `services.ingestion`.
"""
from __future__ import annotations

from app.integrations.base import IntegrationProvider


class SchoologyProvider(IntegrationProvider):
    name = "schoology"
    status = "stub"
