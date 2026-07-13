"""PowerSchool provider (scaffold).

PowerSchool SIS exposes grades/assignments via the PowerQuery / Student
Information APIs (OAuth2 client-credentials, district-hosted). The public
student "PowerSchool Mobile" API is unofficial. To implement:
  1. Register a plugin / obtain client credentials for the district server.
  2. Pull sections, assignments, and stored grades.
  3. Normalize into Atlas via `base.py` helpers.
"""
from __future__ import annotations

from app.integrations.base import IntegrationProvider


class PowerSchoolProvider(IntegrationProvider):
    name = "powerschool"
    status = "stub"
