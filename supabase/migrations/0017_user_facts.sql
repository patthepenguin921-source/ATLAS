-- =====================================================================
-- ATLAS — 0017 · User facts (chat memory)
--
-- Durable facts/preferences Atlas learns about the student from ordinary
-- conversation (e.g. "I learn better with visual examples", "I have
-- practice until 7pm most weeknights") — distinct from `student_knowledge`
-- (per-concept mastery) and from document content. Keyed on (user_id, key)
-- so re-learning the same thing updates it in place instead of piling up
-- duplicates.
-- =====================================================================

create table if not exists public.user_facts (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  key         text not null,           -- short stable label, e.g. "study_style"
  value       text not null,           -- the fact itself, in natural language
  category    text,                    -- preference | goal | constraint | context | other
  source_conversation_id uuid references public.conversations(id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (user_id, key)
);

create index if not exists idx_user_facts_user on public.user_facts(user_id);

alter table public.user_facts enable row level security;
drop policy if exists user_facts_owner on public.user_facts;
create policy user_facts_owner on public.user_facts
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

drop trigger if exists trg_user_facts_updated on public.user_facts;
create trigger trg_user_facts_updated before update on public.user_facts
  for each row execute function public.set_updated_at();
