-- =====================================================================
-- ATLAS — 0014 · Course active/completed status
--
-- Schoology reports whether a section is still active (current grading
-- period) or has ended (`section.active` in the Sections API). Atlas had no
-- column to persist that, so a completed class stayed mixed in with current
-- ones forever. `is_active` lets the sync keep this current, and lets a
-- student manually archive a manually-entered/PowerSchool-only course too.
-- =====================================================================

alter table public.courses
  add column if not exists is_active boolean not null default true;

create index if not exists idx_courses_active
  on public.courses(user_id, is_active);
