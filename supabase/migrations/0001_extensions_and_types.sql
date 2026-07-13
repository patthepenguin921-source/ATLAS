-- =====================================================================
-- ATLAS — 0001 · Extensions & Enum Types
-- The factual-memory foundation. Safe to run multiple times.
-- =====================================================================

-- pgvector powers Atlas's semantic memory (embeddings live in Postgres
-- alongside the structured data, so a single query can join facts + meaning).
create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid()
create extension if not exists pg_trgm;     -- fuzzy text search

-- ---------------------------------------------------------------------
-- Enum types (created idempotently via DO blocks)
-- ---------------------------------------------------------------------
do $$ begin
  create type assignment_category as enum (
    'homework','classwork','quiz','test','exam','project','essay',
    'lab','discussion','presentation','reading','participation','other'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type assignment_status as enum (
    'not_started','in_progress','submitted','graded','missing','excused','late'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type document_type as enum (
    'pdf','powerpoint','notes','announcement','study_guide','essay',
    'practice_problems','rubric','personal_note','email','image','other'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type concept_edge_type as enum (
    'prerequisite','builds_on','related','part_of','applies_to','contrasts_with'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type integration_provider as enum (
    'schoology','powerschool','blackboard','google_classroom','canvas','manual'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type sync_status as enum ('idle','running','success','error');
exception when duplicate_object then null; end $$;

do $$ begin
  create type agent_role as enum ('planner','tutor','analyst','archivist','coach','general');
exception when duplicate_object then null; end $$;

do $$ begin
  create type message_role as enum ('user','assistant','system','tool');
exception when duplicate_object then null; end $$;
