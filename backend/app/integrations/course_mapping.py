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
from typing import Any

# ---------------------------------------------------------------------
# Never imported — non-academic blocks Schoology lists as sections but that
# have no place in Atlas at all (not even as a club).
# ---------------------------------------------------------------------
_EXCLUDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\blunch\b", re.I),
    re.compile(r"\bambush\b", re.I),  # covers "AMBUSH 23" and any cohort variant
    re.compile(r"\bcat\s*time\b", re.I),  # non-academic advisory block
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


# ---------------------------------------------------------------------
# Known Schoology course links — confirmed directly from this account's own
# browser session: course id, display name, the parent-preview student uid
# where the confirmed link needs one, and (most importantly) `materials_url`
# — the *exact*, literal URL that link is, verbatim. Nothing here is
# derived/guessed at: the whole point of this table is that the debug tools
# (and, via `merge_known_sections`, anything that inherits its `id`) hit only
# this one URL per course, never any other candidate shape (district
# subdomain, the other of `/materials` vs `/preview/{uid}/parent`, …) the
# way undirected discovery does — see `SchoologyScraperClient.
# walk_known_url`. `student_uid` is `None` for the two courses whose
# confirmed link is a direct `/materials` URL rather than a per-student
# `/preview/{uid}/parent` one.
# ---------------------------------------------------------------------
KNOWN_SECTIONS: tuple[dict[str, str | None], ...] = (
    {"id": "8435659601", "name": "AP Biology: Section 2", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435659601/preview/23381548/parent"},
    {"id": "8435669068", "name": "AP Calculus AB: Section 1", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435669068/preview/23381548/parent"},
    {"id": "8435650700", "name": "DE Entrprnrshp: Section 901", "student_uid": None,
     "materials_url": "https://app.schoology.com/course/8435650700/materials"},
    {"id": "8435650657", "name": "DE Intro Bus: Section 901", "student_uid": None,
     "materials_url": "https://app.schoology.com/course/8435650657/materials"},
    {"id": "8435659627", "name": "Bio PreLab HN: Section 2", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435659627/preview/23381548/parent"},
    {"id": "8435655035", "name": "Physics 1 H Ext Lab: Section 1", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435655035/preview/23381548/parent"},
    {"id": "8435659618", "name": "AP Physics I: Section 1", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435659618/preview/23381548/parent"},
    {"id": "8435669783", "name": "AP Calculus BC: Section 1", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435669783/preview/23381548/parent"},
    {"id": "8435652619", "name": "AP English Lang: Section 2", "student_uid": "23381548",
     "materials_url": "https://app.schoology.com/course/8435652619/preview/23381548/parent"},
)


def materials_url_for(section_id: str) -> str | None:
    """The confirmed, exact materials URL for `section_id`, if it's one of
    the courses in `KNOWN_SECTIONS` — `None` otherwise. Any sync/debug code
    that already has a section id in hand (whether from the API, a linked
    course, or fresh discovery) can call this directly to know whether it
    should walk that one exact URL (`SchoologyScraperClient.walk_known_url`)
    instead of guessing across candidate shapes (`walk_materials`), with no
    need to have gone through `merge_known_sections` first. Looks up
    `KNOWN_SECTIONS` fresh on every call (rather than a dict cached at
    import time) so tests can monkeypatch it and have this reflect the
    change immediately."""
    return next((k.get("materials_url") for k in KNOWN_SECTIONS if k["id"] == section_id), None)


def merge_known_sections(
    sections: list[dict[str, Any]], student_uid: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Union `sections` (whatever an API section listing, a prior sync's
    already-linked courses, or a fresh login-session discovery already
    found — each is keyed by section id, with at least `id`/`name`) with
    `KNOWN_SECTIONS`, by section id. A section discovery already found is
    left as-is (except `materials_url`, backfilled onto it too when its id
    matches a known one, so a stale/incomplete discovered entry still gets
    pinned to the exact confirmed URL); a known one missing from it is
    added, so a caller (currently just the Schoology debug tools — see
    `SchoologyProvider._probe_sections`) can always reach these courses'
    real materials pages regardless of whether discovery itself worked.

    `student_uid` is backfilled from the first known entry that carries one
    when not already known — the same student uid applies to every course in
    this parent account regardless of which course happened to supply it."""
    by_id = {s["id"]: dict(s) for s in sections if s.get("id")}
    for known in KNOWN_SECTIONS:
        entry = by_id.setdefault(known["id"], {"id": known["id"], "name": known["name"]})
        if not entry.get("name"):
            entry["name"] = known["name"]
        if known.get("student_uid") and not entry.get("student_uid"):
            entry["student_uid"] = known["student_uid"]
        if known.get("materials_url") and not entry.get("materials_url"):
            entry["materials_url"] = known["materials_url"]
    if not student_uid:
        student_uid = next((k["student_uid"] for k in KNOWN_SECTIONS if k.get("student_uid")), None)
    return list(by_id.values()), student_uid
