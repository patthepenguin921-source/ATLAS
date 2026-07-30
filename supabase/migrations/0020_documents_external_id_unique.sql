-- =====================================================================
-- ATLAS — 0020 · Prevent duplicate documents rows per external source
--
-- Live evidence: two `documents` rows existed for the exact same
-- Schoology-scraped item (identical user_id/external_id/external_source)
-- after two overlapping sync attempts each raced the application's own
-- select-then-insert existence check -- nothing at the DB level stopped a
-- second INSERT from creating a second row once that race was lost. Each
-- duplicate independently ran the "at a glance" schedule extraction,
-- producing two different sets of calendar_events/assignments for what
-- was really one document -- exactly the "not consistent"
-- duplication reported by a user.
--
-- Partial (only when both columns are actually set, since a manually
-- uploaded document has no external_id/external_source at all) so this
-- never conflicts with manual uploads. Makes the ingest path's insert
-- conflict-safe going forward -- see `_ingest_file`/`_ingest_external_page`
-- in `app.integrations.schoology`, which now catch a conflict here and
-- reuse the winning row instead of proceeding with a duplicate.
-- =====================================================================

create unique index if not exists documents_user_external_unique
  on public.documents (user_id, external_id, external_source)
  where external_id is not null and external_source is not null;
