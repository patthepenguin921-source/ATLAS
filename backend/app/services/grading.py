"""Shared grading vocabulary -- one place so the weight a student picks
when adding an assignment (`app.agents.tools`) and the risk/grade math that
reads it back (`app.services.analytics`, and `recompute_course_grade` in
Postgres) never drift out of sync with each other.
"""
from __future__ import annotations

# Fraction of a course's grade a "minor" vs "major" assignment counts for,
# stored directly in the already-existing `assignments.weight` column (see
# supabase/migrations/0002_core_tables.sql's "category weight if known"
# comment, previously never written by anything) and, via
# `recompute_course_grade`, folded into `courses.current_grade` the same
# way `grades.weight` already was.
ASSIGNMENT_WEIGHTS: dict[str, float] = {"minor": 0.3, "major": 0.7}
