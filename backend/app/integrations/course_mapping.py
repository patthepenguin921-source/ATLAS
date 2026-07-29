"""Course-name mapping rules shared by every sync provider (Schoology,
PowerSchool, ...).

Each provider reports its own classes/sections under its own naming
convention, with no reliable field connecting "the same real class" across
two systems, or (Schoology specifically) distinguishing an academic class
from a lunch/advisory block or a club. This module encodes the school's
actual naming conventions as data, kept separate from each provider's sync
logic so the rules are easy to find and tune without touching the sync
mechanics, plus the shared, provider-agnostic logic (`resolve_grouped_
course_id`, `names_match`) that turns a matched name into the right
`courses` row. All matching is done against a provider's own display name
for the class/section (Schoology's section title, PowerSchool's course
name, ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.supabase_client import eq, supabase

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


def compute_present_group_semesters(names: Iterable[str]) -> dict[str, set[str]]:
    """Which semesters of each course group actually showed up among
    `names` (one sync's worth of section/class display names, from any
    provider) -- the "real evidence" `resolve_grouped_course_id` needs to
    tell an actually-split class apart from a plain, already-reconciled one
    that merely shares a group's non-distinctive half's naming pattern."""
    present: dict[str, set[str]] = {}
    for name in names:
        gm = match_group(name)
        if gm:
            group, member = gm
            present.setdefault(group.key, set()).add(member.semester)
    return present


async def resolve_grouped_course_id(
    *, provider_name: str, user_id: str, external_id: str, display_name: str,
    course_code: str | None, present_group_semesters: dict[str, set[str]],
    extra_fields: dict[str, Any] | None = None, extra_meta: dict[str, Any] | None = None,
) -> str | None:
    """If `display_name` matches one of `COURSE_GROUPS`, resolve (creating if
    needed) the merged course row for it and return its id; `None` if it
    doesn't match any group (or matches but there's no real evidence this
    particular class is actually split that way -- see below), so the
    caller falls through to its own ordinary name-based reconciliation.
    Shared by every provider that can encounter one of these split classes
    (Schoology, PowerSchool, ...) so e.g. Schoology's "AP Biology"/"Bio
    PreLab HN" and PowerSchool's "AP Biology"/"Bio PreLab HN" (or whatever
    that district's SIS happens to call the same two halves) both land on
    the same two linked rows instead of each provider growing its own,
    duplicate pair.

    Only actually splits into the group when there's real evidence: either
    this class is the distinctive HN-prep-lab half (a name like "Physics 1 H
    Ext Lab" doesn't happen to a plain, already-existing course), or both
    halves showed up in `present_group_semesters` for this same sync.
    Otherwise a plain, already-reconciled course (e.g. a stand-alone "AP
    Biology" with no lab counterpart) would get needlessly split.

    A grouped row's `external_id`/`external_source` belong to whichever
    provider most recently synced it, not a fixed single owner -- unlike an
    ordinary (non-grouped) course link, which only ever adds the linking
    provider's own id into `metadata` and never touches a row's existing
    `external_id`/`external_source` (see each provider's own step 2). Since
    `metadata.course_group` + `semester`, not those two columns, is the
    stable key every provider relinks a group's rows through, two providers
    trading sync order freely never lose the link -- each still stashes its
    own identifying field in `extra_meta` (e.g. `schoology_section_id` /
    `powerschool_ccid`) for its own idempotent re-sync in the meantime."""
    group_match = match_group(display_name)
    if not group_match:
        return None
    group, member = group_match
    if not (member.has_hn_prep_lab or len(present_group_semesters.get(group.key, set())) >= 2):
        return None

    existing = await supabase.select(
        "courses", columns="id,metadata",
        filters={"user_id": eq(user_id), "external_id": eq(external_id),
                 "external_source": eq(provider_name)}, limit=1,
    )
    group_rows = await supabase.select(
        "courses", columns="id,semester,linked_course_id,metadata",
        filters={"user_id": eq(user_id), "metadata->>course_group": eq(group.key)},
    ) or []

    patch: dict[str, Any] = {
        "name": group.canonical_name, "code": course_code or None,
        "semester": member.semester, "course_level": member.course_level,
        "has_hn_prep_lab": member.has_hn_prep_lab, "has_ap_prep_lab": member.has_ap_prep_lab,
        "external_id": external_id, "external_source": provider_name,
        **(extra_fields or {}),
    }
    meta_extra = {"course_group": group.key, **(extra_meta or {})}

    if existing:
        row_id = existing[0]["id"]
        meta = {**(existing[0].get("metadata") or {}), **meta_extra}
        await supabase.update("courses", {**patch, "metadata": meta}, filters={"id": eq(row_id)})
        return row_id

    # Reuse the group's row for this semester if one already exists (e.g.
    # this same class re-syncing under a slightly different id, or a
    # different provider having already created/claimed this semester).
    same_semester = next((r for r in group_rows if r.get("semester") == member.semester), None)
    if same_semester:
        meta = {**(same_semester.get("metadata") or {}), **meta_extra}
        await supabase.update(
            "courses", {**patch, "metadata": meta}, filters={"id": eq(same_semester["id"])}
        )
        return same_semester["id"]

    # First row for the group becomes the root; later semesters link to it.
    root = next((r for r in group_rows if not r.get("linked_course_id")), None)
    patch["metadata"] = meta_extra
    if root:
        patch["linked_course_id"] = root["id"]
    created = await supabase.insert("courses", {**patch, "user_id": user_id})
    return created[0]["id"]


# ---------------------------------------------------------------------
# Cross-system course-name matching -- lets a provider recognize that its
# own section/class is the very same real class another provider (or a
# manually-entered course) already has a row for, so the two share one
# `courses` row instead of duplicating it.
# ---------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """Lowercase alphanumeric tokens for cross-system course matching."""
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


# Course-name tokens that carry no discriminating signal when matching one
# provider's section/class name to another's (both label the same class).
_STOPWORDS = {"the", "of", "and", "a", "an", "period", "sec", "section"}


def name_tokens(name: str) -> set[str]:
    return {t for t in normalize_name(name).split() if t and t not in _STOPWORDS}


def names_match(a: str, b: str) -> bool:
    """True if two course names very likely refer to the same class. Uses
    exact normalized equality, or one token set being a subset of the other
    (so "AP Biology" matches "AP Biology - Sec 1"). Deliberately conservative:
    it will NOT merge "AP Calculus AB" with "AP Calculus BC" (a differing
    token blocks the match) — a missed link just leaves a separate course,
    whereas a wrong merge corrupts two classes' data."""
    na, nb = normalize_name(a), normalize_name(b)
    if na and na == nb:
        return True
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


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
