"""Course-name mapping rules for the Schoology sync.

Schoology reports every section a student is enrolled in — academic classes,
lunch/advisory blocks, and clubs — with no reliable field distinguishing them.
This module encodes the school's actual naming conventions as data, kept
separate from `schoology.py`'s sync logic so the rules are easy to find and
tune without touching the sync mechanics. All matching is done against a
section's ``display_name`` (Schoology's course/section title).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------
# Never imported — non-academic blocks Schoology lists as sections but that
# have no place in Atlas at all (not even as a club).
# ---------------------------------------------------------------------
_EXCLUDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\blunch\b", re.I),
    re.compile(r"\bambush\b", re.I),  # covers "AMBUSH 23" and any cohort variant
)


def is_excluded(name: str) -> bool:
    name = name or ""
    return any(p.search(name) for p in _EXCLUDED_PATTERNS)


# ---------------------------------------------------------------------
# Clubs/activities (DECA, Interact Club, …) — tracked separately from
# academic courses (see the `clubs` table) rather than mixed into GPA/course
# data. The generic "club" pattern catches most of them by itself; named
# exceptions (orgs whose name doesn't contain the word "club") are added
# individually as they show up.
# ---------------------------------------------------------------------
_CLUB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bclub\b", re.I),
    re.compile(r"\bdeca\b", re.I),
)


def is_club(name: str) -> bool:
    name = name or ""
    return any(p.search(name) for p in _CLUB_PATTERNS)


# ---------------------------------------------------------------------
# Course groups — some "real" courses are split across two differently-named
# Schoology sections, one per semester (an HN-weighted prep/lab half feeding
# an AP-weighted half, or an AB half + a BC half of the same class). Atlas
# displays each group as a single course with two linked semester rows (the
# same `linked_course_id`/`semester` mechanism as `courses.split-semesters`,
# migration 0009) instead of as two unrelated classes.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class GroupMember:
    pattern: re.Pattern[str]
    semester: str  # 's1' | 's2'
    course_level: str  # matches the `course_level` enum
    has_hn_prep_lab: bool = False
    has_ap_prep_lab: bool = False


@dataclass(frozen=True)
class CourseGroup:
    key: str  # stable slug stored in metadata->>course_group
    canonical_name: str  # what the merged course is displayed as
    members: tuple[GroupMember, ...] = field(default_factory=tuple)


COURSE_GROUPS: tuple[CourseGroup, ...] = (
    # AP Physics: an HN-weighted "prep lab" semester (e.g. "Physics 1 H Ext
    # Lab") feeds into the AP-weighted semester. Both halves are the same
    # class split by semester, not by content like AB/BC.
    CourseGroup(
        key="ap_physics",
        canonical_name="AP Physics",
        members=(
            GroupMember(
                pattern=re.compile(r"physics.*\b(h|honors|hn|ext\s*lab|prep\s*lab)\b", re.I),
                semester="s1", course_level="honors", has_hn_prep_lab=True,
            ),
            GroupMember(
                pattern=re.compile(r"\bap\b.*physics", re.I),
                semester="s2", course_level="ap", has_ap_prep_lab=True,
            ),
        ),
    ),
    # AP Biology: same HN-lab -> AP-credit split as AP Physics.
    CourseGroup(
        key="ap_biology",
        canonical_name="AP Biology",
        members=(
            GroupMember(
                pattern=re.compile(r"bio.*\b(h|honors|hn|prep\s*lab)\b", re.I),
                semester="s1", course_level="honors", has_hn_prep_lab=True,
            ),
            GroupMember(
                pattern=re.compile(r"\bap\b.*bio", re.I),
                semester="s2", course_level="ap", has_ap_prep_lab=True,
            ),
        ),
    ),
    # AP Calculus AB/BC: unlike Physics/Bio, both halves are already
    # AP-weighted — AB and BC are just the two semesters of one class,
    # displayed together as "AP Calculus" (no HN prep lab either half).
    CourseGroup(
        key="ap_calc_bc",
        canonical_name="AP Calculus",
        members=(
            GroupMember(
                pattern=re.compile(r"\bap\b.*calc(ulus)?.*\bab\b", re.I),
                semester="s1", course_level="ap",
            ),
            GroupMember(
                pattern=re.compile(r"\bap\b.*calc(ulus)?.*\bbc\b", re.I),
                semester="s2", course_level="ap",
            ),
        ),
    ),
)


def match_group(name: str) -> tuple[CourseGroup, GroupMember] | None:
    """First course group + member whose pattern matches `name`, if any."""
    name = name or ""
    for group in COURSE_GROUPS:
        for member in group.members:
            if member.pattern.search(name):
                return group, member
    return None


# ---------------------------------------------------------------------
# GPA-weight inference for ordinary (non-grouped) courses. Schoology has no
# "is this AP/Honors/DE" field, so a brand-new course otherwise defaults to
# `course_level='regular'` — silently wrong for e.g. "AP English Lang". This
# only fills in a sensible default when *creating* a course; it never
# overwrites a level a user (or another matched course) already set.
# ---------------------------------------------------------------------
_LEVEL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bib\b", re.I), "ib"),
    (re.compile(r"\bd\.?e\.?\b|dual\s*enroll", re.I), "dual_enrollment"),
    (re.compile(r"\bap\b", re.I), "ap"),
    (re.compile(r"\bh(?:on(?:ors)?)?\b", re.I), "honors"),
)


def infer_course_level(name: str) -> str:
    name = name or ""
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.search(name):
            return level
    return "regular"
