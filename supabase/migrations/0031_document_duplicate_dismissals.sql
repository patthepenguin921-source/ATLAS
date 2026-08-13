-- =====================================================================
-- ATLAS — 0031 · Dismissed document-duplicate suggestions
--
-- The documents page now surfaces pairs of documents that look like the
-- same file added twice under separate rows (e.g. once via Google Drive
-- import, once via a manual upload of the same PDF) so the student can
-- merge them -- see app.services.document_dedupe. This table remembers a
-- pair the student explicitly said is "not the same document" so the same
-- suggestion doesn't keep coming back on every page load. Merging removes
-- one side of the pair entirely, so it never needs a row here.
-- =====================================================================

create table if not exists public.document_duplicate_dismissals (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references auth.users(id) on delete cascade,
  -- Always stored with document_id_a < document_id_b (as text) so a
  -- dismissed pair is a single row regardless of which order the detector
  -- happens to compare the two documents in on a later scan.
  document_id_a      uuid not null references public.documents(id) on delete cascade,
  document_id_b      uuid not null references public.documents(id) on delete cascade,
  created_at         timestamptz not null default now(),
  unique (user_id, document_id_a, document_id_b)
);

create index if not exists idx_document_dup_dismissals_user
  on public.document_duplicate_dismissals(user_id);

alter table public.document_duplicate_dismissals enable row level security;
drop policy if exists document_duplicate_dismissals_owner on public.document_duplicate_dismissals;
create policy document_duplicate_dismissals_owner on public.document_duplicate_dismissals
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
