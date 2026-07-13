-- =====================================================================
-- ATLAS — 0009 · Course semesters & linked semester courses
--
-- Some classes are "technically two classes": e.g. an HN Prep Lab semester
-- weighted 5.5 followed by an AP semester weighted 6.0. Rather than cram two
-- weights onto one row, Atlas lets a course be split into two linked rows —
-- one per semester — that share a name/teacher/term but track grades and GPA
-- independently (predicted_gpa already sums per-course).
--
--   semester          'full_year' (default) | 's1' | 's2'
--   linked_course_id  points both halves at a shared grouping id so the UI
--                     can show S1/S2 chips and let you jump between them.
-- =====================================================================

alter table public.courses
  add column if not exists semester         text not null default 'full_year',
  add column if not exists linked_course_id uuid references public.courses(id) on delete set null;

do $$ begin
  alter table public.courses
    add constraint courses_semester_check
    check (semester in ('full_year', 's1', 's2'));
exception when duplicate_object then null; end $$;

create index if not exists idx_courses_linked
  on public.courses(user_id, linked_course_id);
