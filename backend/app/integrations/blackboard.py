"""Blackboard provider (scaffold).

Blackboard Learn exposes a REST API (OAuth2) at /learn/api/public/v1/. To
implement:
  1. Register a REST application in the Blackboard Developer Portal.
  2. GET /courses (enrollments), /courses/{id}/contents, /gradebook/columns.
  3. Normalize into Atlas via `base.py` helpers and ingest attached files.
"""
from __future__ import annotations

from app.integrations.base import IntegrationProvider


class BlackboardProvider(IntegrationProvider):
    name = "blackboard"
    status = "stub"
