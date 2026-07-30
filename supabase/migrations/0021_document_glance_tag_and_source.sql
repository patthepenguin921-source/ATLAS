-- =====================================================================
-- ATLAS — 0021 · "glance" document tag + manual doc_type override tracking
--
-- Live evidence: an "at a glance" schedule document (a real AP Calculus BC
-- "Unit 1 at a Glance") was getting classified by the Archivist's general
-- enrichment pass as `study_guide` -- a reasonable guess from the content
-- alone, but wrong: Atlas already knows definitively that a document is a
-- schedule doc (`metadata.is_glance`/`is_recurring_glance`, set by
-- app.services.schedule_extraction) well before enrichment ever runs, so
-- it shouldn't need to guess at all for these.
--
-- Adds a `glance` value to the `document_type` enum so these get their own
-- honest tag, plus `doc_type_source` (mirroring `importance_source` from
-- migration 0016) so a student's manual re-tag of a document is never
-- quietly overwritten by a later re-enrichment or re-sync -- only ever set
-- `doc_type` automatically when it's still AI/system-sourced or unset.
-- =====================================================================

alter type public.document_type add value if not exists 'glance';
