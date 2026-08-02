-- =====================================================================
-- ATLAS — 0029 · Manual risk override, assignment units, practice
-- history, and a standalone Google Drive connection
--
-- Bundles four small, independent additions requested together:
--   1. assignments.risk_override -- lets a student directly set an
--      assignment's risk badge (low/medium/high/extreme) instead of only
--      ever nudging it indirectly via weight/difficulty/points (see
--      app.services.analytics._risk_level).
--   2. assignments.folder_id -- files an assignment under a class's
--      existing unit/subunit folder tree (see 0024_folders.sql), the same
--      way documents already can.
--   3. practice_sessions -- persists a generated practice quiz/test (see
--      app.agents.tutor.Tutor.quiz) so past practice is browsable instead
--      of vanishing the moment the page reloads.
--   4. 'google_drive' added to integration_provider -- lets a student
--      connect Google Drive on its own, independent of Schoology (see
--      app.integrations.google_oauth), so the chat agent can search and
--      pull a document from it on request even without Schoology synced.
-- =====================================================================

alter table public.assignments
  add column if not exists risk_override text,
  add column if not exists folder_id uuid references public.folders(id) on delete set null;

do $$ begin
  alter table public.assignments
    add constraint assignments_risk_override_check
    check (risk_override is null or risk_override in ('low', 'medium', 'high', 'extreme'));
exception when duplicate_object then null; end $$;

create index if not exists idx_assignments_folder on public.assignments(folder_id);

alter type public.integration_provider add value if not exists 'google_drive';

create table if not exists public.practice_sessions (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  course_id     uuid references public.courses(id) on delete set null,
  folder_id     uuid references public.folders(id) on delete set null,
  topic         text not null,
  mode          text not null default 'quiz',    -- quiz | test
  source        text not null default 'materials', -- materials | internet
  questions     jsonb not null default '[]'::jsonb,
  score         numeric(5,2),                     -- self-reported correct/total, if graded
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

do $$ begin
  alter table public.practice_sessions
    add constraint practice_sessions_mode_check check (mode in ('quiz', 'test'));
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.practice_sessions
    add constraint practice_sessions_source_check check (source in ('materials', 'internet'));
exception when duplicate_object then null; end $$;

create index if not exists idx_practice_sessions_user on public.practice_sessions(user_id, created_at desc);
create index if not exists idx_practice_sessions_course on public.practice_sessions(user_id, course_id);

alter table public.practice_sessions enable row level security;
drop policy if exists practice_sessions_owner on public.practice_sessions;
create policy practice_sessions_owner on public.practice_sessions
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
