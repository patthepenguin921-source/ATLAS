-- =====================================================================
-- ATLAS — 0019 · Cron-based document processing
--
-- Uploading used to finish indexing (chunk/embed) + AI enrichment inline,
-- via a FastAPI BackgroundTask, before responding to the request that
-- created it. In production on Cloud Run that silently stalled forever:
-- Cloud Run only allocates CPU to a container while it's actively serving
-- a request, so a background task kept alive past the response getting
-- sent can simply never get scheduled again. Vercel's serverless runtime
-- has the same class of risk.
--
-- The fix: a document upload now only stores the file + a bare row
-- (`ingested: false`); indexing + enrichment happen in a scheduled cron
-- endpoint instead (same pattern as the PowerSchool/Schoology sync and
-- storage-cleanup jobs), which always gets real CPU because a scheduler
-- is actively calling it. `processing_started_at` lets that cron claim a
-- batch of pending documents without two overlapping runs double-
-- processing the same one. `auto_title`/`enrich` persist what the
-- original upload request knew (whether to let the AI rename a
-- filename-derived placeholder, whether to enrich at all) since the cron
-- runs in a completely separate request with no access to that context.
-- =====================================================================

alter table public.documents
  add column if not exists processing_started_at timestamptz,
  add column if not exists auto_title boolean not null default false,
  add column if not exists enrich boolean not null default true;

create index if not exists idx_documents_pending_processing
  on public.documents(processing_started_at)
  where not ingested and ingest_error is null;
