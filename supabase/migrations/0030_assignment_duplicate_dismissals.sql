-- =====================================================================
-- ATLAS — 0030 · Dismissed assignment-duplicate suggestions
--
-- The assignments page now surfaces pairs of assignments that look like
-- the same real-world assignment synced twice (e.g. once from PowerSchool,
-- once from Schoology, under different titles) so the student can merge
-- them -- see app.services.assignment_dedupe. This table remembers a pair
-- the student explicitly said is "not a duplicate" so the same suggestion
-- doesn't keep coming back on every page load. Merging removes one side of
-- the pair entirely, so it never needs a row here.
-- =====================================================================

create table if not exists public.assignment_duplicate_dismissals (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references auth.users(id) on delete cascade,
  -- Always stored with assignment_id_a < assignment_id_b (as text) so a
  -- dismissed pair is a single row regardless of which order the detector
  -- happens to compare the two assignments in on a later scan.
  assignment_id_a    uuid not null references public.assignments(id) on delete cascade,
  assignment_id_b    uuid not null references public.assignments(id) on delete cascade,
  created_at         timestamptz not null default now(),
  unique (user_id, assignment_id_a, assignment_id_b)
);

create index if not exists idx_assignment_dup_dismissals_user
  on public.assignment_duplicate_dismissals(user_id);

alter table public.assignment_duplicate_dismissals enable row level security;
drop policy if exists assignment_duplicate_dismissals_owner on public.assignment_duplicate_dismissals;
create policy assignment_duplicate_dismissals_owner on public.assignment_duplicate_dismissals
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
