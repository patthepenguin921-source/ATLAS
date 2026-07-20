-- =====================================================================
-- ATLAS — 0012 · Clubs & course-group linking
--
-- Clubs (DECA, etc.) sync in from Schoology sections that aren't academic
-- classes — tracked separately from `courses` so they never touch GPA/grade
-- data. Kept intentionally minimal (name + basic metadata) for now.
--
-- Also indexes the metadata->>'course_group' key the Schoology provider
-- uses to link split lab/AP sections (e.g. "Physics 1 H Ext Lab" + "AP
-- Physics 1") into one displayed course with linked semester rows.
-- =====================================================================

create table if not exists public.clubs (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  name            text not null,
  advisor         text,
  meeting_info    text,
  external_id     text,
  external_source integration_provider,
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists idx_clubs_user on public.clubs(user_id);

alter table public.clubs enable row level security;
drop policy if exists clubs_owner on public.clubs;
create policy clubs_owner on public.clubs
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create index if not exists idx_courses_group
  on public.courses(user_id, ((metadata->>'course_group')));
