"""Pure unit tests for the Schoology course-name mapping rules."""
from __future__ import annotations

from app.integrations import course_mapping


def test_excludes_lunch_and_ambush():
    assert course_mapping.is_excluded("Lunch A")
    assert course_mapping.is_excluded("7th Period Lunch")
    assert course_mapping.is_excluded("AMBUSH 23")
    assert course_mapping.is_excluded("Ambush - Period 4")
    assert course_mapping.is_excluded("CAT Time")
    assert course_mapping.is_excluded("CAT Time - Period 6")
    assert not course_mapping.is_excluded("AP Biology")


def test_club_detection():
    assert course_mapping.is_club("DECA")
    assert course_mapping.is_club("DECA - Chapter Meeting")
    assert course_mapping.is_club("Lexington High Interact Club")
    assert not course_mapping.is_club("AP Biology")


def test_infer_course_level():
    assert course_mapping.infer_course_level("AP English Lang") == "ap"
    assert course_mapping.infer_course_level("IB History") == "ib"
    assert course_mapping.infer_course_level("DE Intro to Business") == "dual_enrollment"
    assert course_mapping.infer_course_level("Honors Chemistry") == "honors"
    assert course_mapping.infer_course_level("Algebra II") == "regular"


def test_ap_physics_group_matching():
    lab = course_mapping.match_group("Physics 1 H Ext Lab")
    ap = course_mapping.match_group("AP Physics 1")
    assert lab is not None and ap is not None
    lab_group, lab_member = lab
    ap_group, ap_member = ap
    assert lab_group.key == ap_group.key == "ap_physics"
    assert lab_group.canonical_name == "AP Physics"
    assert lab_member.semester == "s1" and lab_member.has_hn_prep_lab
    assert ap_member.semester == "s2" and ap_member.course_level == "ap"


def test_ap_biology_group_matching():
    lab = course_mapping.match_group("Biology 1 Honors Prep Lab")
    ap = course_mapping.match_group("AP Biology")
    assert lab is not None and ap is not None
    assert lab[0].key == ap[0].key == "ap_biology"
    assert lab[1].has_hn_prep_lab
    assert not ap[1].has_hn_prep_lab


def test_ap_calc_ab_bc_group_matching():
    ab = course_mapping.match_group("AP Calculus AB")
    bc = course_mapping.match_group("AP Calculus BC")
    assert ab is not None and bc is not None
    ab_group, ab_member = ab
    bc_group, bc_member = bc
    assert ab_group.key == bc_group.key == "ap_calc_bc"
    assert ab_group.canonical_name == "AP Calculus"
    # Both halves are AP-weighted; neither uses the HN prep-lab flag.
    assert ab_member.semester == "s1" and ab_member.course_level == "ap"
    assert not ab_member.has_hn_prep_lab
    assert bc_member.semester == "s2" and bc_member.course_level == "ap"
    assert not bc_member.has_hn_prep_lab


def test_unrelated_course_does_not_match_a_group():
    assert course_mapping.match_group("English 10") is None
