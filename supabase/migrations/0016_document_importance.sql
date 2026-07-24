-- =====================================================================
-- ATLAS — 0016 · Document importance rating
--
-- The Archivist enrichment step (same pass that already generates
-- summary/keywords/doc_type) infers a starting importance level; the
-- student can override it anytime from the documents page.
-- `importance_source` tracks which — a later re-enrichment never
-- overwrites a manual choice, only ever setting importance itself when
-- it's still AI-sourced (or unset).
-- =====================================================================

alter table public.documents
  add column if not exists importance text not null default 'normal'
    check (importance in ('low', 'normal', 'high')),
  add column if not exists importance_source text
    check (importance_source is null or importance_source in ('ai', 'manual'));

create index if not exists idx_documents_importance
  on public.documents(user_id, importance) where importance <> 'normal';
