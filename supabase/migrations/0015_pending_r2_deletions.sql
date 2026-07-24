-- =====================================================================
-- ATLAS — 0015 · Delayed R2 object deletion
--
-- Deleting a document in the app removes its row immediately (unchanged),
-- but the underlying R2 file is now queued here instead of being removed
-- inline — a 24-hour grace window before the original file is actually
-- gone for good, swept by a scheduled job (see app.services.storage_cleanup
-- / the cron route in app.routers.documents). Backend-only bookkeeping, not
-- user-facing data — RLS is enabled with no policies so only the service
-- role (which bypasses RLS) can touch it; the anon/authenticated keys the
-- browser uses have no access at all.
-- =====================================================================

create table if not exists public.pending_r2_deletions (
  id            uuid primary key default gen_random_uuid(),
  storage_path  text not null,
  requested_at  timestamptz not null default now()
);

create index if not exists idx_pending_r2_deletions_requested_at
  on public.pending_r2_deletions(requested_at);

alter table public.pending_r2_deletions enable row level security;
