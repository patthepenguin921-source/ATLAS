-- =====================================================================
-- ATLAS — 0028 · Document summary/keywords manual-override tracking
--
-- Mirrors the `doc_type_source`/`importance_source`/`folder_source` idiom
-- (0016, 0021, 0022, 0024): a student (or the chat agent's `update_document`
-- tool, see app.agents.tools) can now edit a document's summary/keywords
-- directly, and without this, the next re-enrichment pass (a resync, a
-- retried "Index now") would silently clobber that edit right back to
-- whatever the AI generates. `summary_source = 'manual'` protects it the
-- same way the other three fields are already protected.
-- =====================================================================

alter table public.documents
  add column if not exists summary_source text
    check (summary_source is null or summary_source in ('ai', 'manual'));
