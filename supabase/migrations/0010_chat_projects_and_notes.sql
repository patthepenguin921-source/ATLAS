-- =====================================================================
-- ATLAS — 0010 · Chat projects/tags/archive + assignment notes
--
-- Lets a student organize Ask-Atlas conversations like Claude does: group
-- them into named Projects (folders), tag them by class/subject/unit, and
-- archive or delete them. Also adds a free-form notes field to assignments.
-- =====================================================================

-- Chat projects (folders) --------------------------------------------------
create table if not exists public.chat_projects (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,
  color       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_chat_projects_user on public.chat_projects(user_id);

-- Conversation organization ------------------------------------------------
alter table public.conversations
  add column if not exists project_id uuid references public.chat_projects(id) on delete set null,
  add column if not exists tags       text[] not null default '{}',
  add column if not exists archived   boolean not null default false;

create index if not exists idx_conversations_project on public.conversations(user_id, project_id);

-- Assignment notes ---------------------------------------------------------
alter table public.assignments
  add column if not exists notes text;

-- RLS: owner-only, matching every other user-scoped table -------------------
alter table public.chat_projects enable row level security;
drop policy if exists chat_projects_owner on public.chat_projects;
create policy chat_projects_owner on public.chat_projects
  using (user_id = auth.uid()) with check (user_id = auth.uid());
