-- =====================================================================
-- ATLAS — 0018 · Correct GPA scale to match the actual SC Uniform Grading
-- Policy (SC State Board of Education, 10-Point Scale, 2016–present)
--
-- 0013's continuous formula and bonus values (base = 4.0-(100-pct)*0.1,
-- bonus +1.0/+1.5/+2.0 for regular/honors/AP) were invented — they don't
-- match South Carolina's actual statewide table (SBE "Administrative
-- Procedures, South Carolina Uniform Grading Policy", Table A-2), and it
-- also applied that sliding scale to the *unweighted* GPA, which by
-- convention is a flat 4.0-max scale (A=4.0/B=3.0/C=2.0/D=1.0/F=0 by letter
-- band, no course-level bonus, no sliding above the band floor) — not one
-- of SC's own weighted tracks.
--
-- Weighted (p_weighted = true) now matches the official table exactly:
--   quality points = (pct - 50) * 0.1   for pct > 50, else 0
--   + 0.0  College Prep (regular)
--   + 0.5  Honors
--   + 1.0  AP / IB / Dual Credit ("Extended Quality Points" in the SBE doc)
-- Verified row-by-row, e.g.: pct=100 -> 5.0/5.5/6.0, pct=90 -> 4.0/4.5/5.0,
-- pct=59 -> 0.9/1.4/1.9, pct<=50 -> 0.0 (all tracks).
--
-- Unweighted (p_weighted = false) is now the conventional flat scale:
--   90-100 -> 4.0, 80-89 -> 3.0, 70-79 -> 2.0, 60-69 -> 1.0, else 0.0
-- regardless of course_level — an all-A's student sits at exactly 4.0, not
-- above it.
--
-- has_hn_prep_lab previously forced a nonstandard +1.5 override on the
-- weighted scale; there's no such tier in the real policy, so it now just
-- gets the Honors bonus (+0.5), same as before, and has no effect at all
-- on the (course-level-agnostic) unweighted scale.
-- =====================================================================

create or replace function public.percentage_to_gpa(
  pct numeric,
  p_course_level course_level default 'regular',
  p_has_hn_prep_lab boolean default false,
  p_weighted boolean default true
)
returns numeric language sql immutable as $$
  select case
    when pct is null then null
    when not p_weighted then
      case
        when pct >= 90 then 4.0
        when pct >= 80 then 3.0
        when pct >= 70 then 2.0
        when pct >= 60 then 1.0
        else 0.0
      end
    when pct <= 50 then 0.0
    else (pct - 50) * 0.1
      + case
          when p_course_level in ('ap', 'dual_enrollment', 'ib') then 1.0
          when p_course_level = 'honors' or p_has_hn_prep_lab then 0.5
          else 0.0
        end
  end;
$$;
