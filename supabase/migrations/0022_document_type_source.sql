-- =====================================================================
-- ATLAS — 0022 · doc_type_source (manual-override tracking)
--
-- Split from 0021 so the new `document_type` enum value it adds is never
-- referenced in the same transaction it's created in (Postgres forbids
-- that for a brand-new enum label on older versions and it's not worth
-- relying on the PG12+ relaxation holding for every environment this runs
-- against).
--
-- Same shape as `importance_source` (migration 0016): null/'ai' means
-- Atlas is still free to set `doc_type` on the next enrichment or glance
-- re-sync; 'manual' means a student picked it from the documents page and
-- it must never be silently overwritten again.
-- =====================================================================

alter table public.documents
  add column if not exists doc_type_source text
    check (doc_type_source is null or doc_type_source in ('ai', 'manual', 'system'));
