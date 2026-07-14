-- =====================================================================
-- ATLAS — 0011 · Document auto-classification review flags
--
-- Bulk upload auto-detects which course a file belongs to via the LLM.
-- Low-confidence guesses are still assigned (never left unfiled) but
-- flagged so the student can review/correct them from the documents page.
-- =====================================================================

alter table public.documents
  add column if not exists needs_review     boolean not null default false,
  add column if not exists course_confidence numeric(4,3);

create index if not exists idx_documents_needs_review
  on public.documents(user_id, needs_review) where needs_review;
