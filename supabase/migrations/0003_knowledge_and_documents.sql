-- =====================================================================
-- ATLAS — 0003 · Documents, Semantic Memory & Knowledge Graph
-- =====================================================================

-- Embedding dimension. Default 1024 (voyage-3 / local fallback).
-- If you switch to a model with a different dimension, change the
-- vector(1024) columns below and re-embed.

-- ---------------------------------------------------------------------
-- Documents — original files, linked to storage + structured records
-- ---------------------------------------------------------------------
create table if not exists public.documents (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  course_id       uuid references public.courses(id) on delete set null,
  assignment_id   uuid references public.assignments(id) on delete set null,
  title           text not null,
  doc_type        document_type not null default 'other',
  storage_path    text,                     -- path in Supabase Storage bucket
  mime_type       text,
  size_bytes      bigint,
  page_count      integer,
  -- extracted text + AI-generated metadata (Archivist agent)
  extracted_text  text,
  summary         text,
  keywords        text[],
  tags            text[],
  ingested        boolean not null default false,
  ingest_error    text,
  external_id     text,
  external_source integration_provider,
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Document chunks — semantic units with embeddings (pgvector)
-- ---------------------------------------------------------------------
create table if not exists public.document_chunks (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  document_id   uuid not null references public.documents(id) on delete cascade,
  chunk_index   integer not null,
  content       text not null,
  token_count   integer,
  embedding     vector(1024),
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  unique (document_id, chunk_index)
);

-- ---------------------------------------------------------------------
-- Concepts — nodes in Atlas's knowledge graph
-- ---------------------------------------------------------------------
create table if not exists public.concepts (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  course_id     uuid references public.courses(id) on delete set null,
  name          text not null,
  slug          text,
  description   text,
  subject       text,
  embedding     vector(1024),
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (user_id, name)
);

-- ---------------------------------------------------------------------
-- Concept edges — relationships (prerequisite, builds_on, related …)
-- ---------------------------------------------------------------------
create table if not exists public.concept_edges (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  from_concept  uuid not null references public.concepts(id) on delete cascade,
  to_concept    uuid not null references public.concepts(id) on delete cascade,
  edge_type     concept_edge_type not null default 'related',
  weight        numeric(4,3) default 1.0,
  created_at    timestamptz not null default now(),
  unique (from_concept, to_concept, edge_type)
);

-- Link concepts <-> assignments and documents
create table if not exists public.assignment_concepts (
  assignment_id uuid not null references public.assignments(id) on delete cascade,
  concept_id    uuid not null references public.concepts(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  relevance     numeric(4,3) default 1.0,
  primary key (assignment_id, concept_id)
);

create table if not exists public.document_concepts (
  document_id   uuid not null references public.documents(id) on delete cascade,
  concept_id    uuid not null references public.concepts(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  relevance     numeric(4,3) default 1.0,
  primary key (document_id, concept_id)
);

-- ---------------------------------------------------------------------
-- Student knowledge model — Atlas's estimate of understanding per concept
-- Updated automatically after quizzes, assignments, tests.
-- ---------------------------------------------------------------------
create table if not exists public.student_knowledge (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users(id) on delete cascade,
  concept_id          uuid not null references public.concepts(id) on delete cascade,
  confidence          numeric(4,3) not null default 0.5,   -- 0..1 self/AI confidence
  mastery             numeric(4,3) not null default 0.0,   -- 0..1 demonstrated mastery
  retention           numeric(4,3) not null default 0.0,   -- 0..1 current retention estimate
  -- spaced-repetition scheduling (SM-2-ish)
  ease_factor         numeric(4,3) not null default 2.5,
  interval_days       integer not null default 0,
  repetitions         integer not null default 0,
  last_reviewed_at    timestamptz,
  next_review_at      timestamptz,
  predicted_forget_at timestamptz,
  evidence_count      integer not null default 0,
  metadata            jsonb not null default '{}'::jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (user_id, concept_id)
);

-- ---------------------------------------------------------------------
-- Mistakes — recorded errors, for pattern analysis ("what do I keep
-- getting wrong?")
-- ---------------------------------------------------------------------
create table if not exists public.mistakes (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  course_id     uuid references public.courses(id) on delete set null,
  assignment_id uuid references public.assignments(id) on delete set null,
  concept_id    uuid references public.concepts(id) on delete set null,
  description   text not null,
  mistake_type  text,             -- conceptual | careless | procedural | knowledge_gap
  correction    text,
  occurred_at   timestamptz not null default now(),
  resolved      boolean not null default false,
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Daily plans & weekly reviews (generated by Planner / Coach)
-- ---------------------------------------------------------------------
create table if not exists public.daily_plans (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  plan_date     date not null,
  summary       text,
  blocks        jsonb not null default '[]'::jsonb,   -- [{start,end,task,course_id,...}]
  priorities    jsonb not null default '[]'::jsonb,
  estimated_minutes integer,
  motivational_note text,
  generated_by  agent_role default 'planner',
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (user_id, plan_date)
);

create table if not exists public.weekly_reviews (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  week_start    date not null,
  accomplishments text,
  grade_changes jsonb not null default '{}'::jsonb,
  knowledge_gained jsonb not null default '[]'::jsonb,
  knowledge_weakening jsonb not null default '[]'::jsonb,
  productivity  jsonb not null default '{}'::jsonb,
  recommendations text,
  goals         jsonb not null default '[]'::jsonb,
  narrative     text,
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (user_id, week_start)
);

-- ---------------------------------------------------------------------
-- Progress metrics — time-series of any measurable quantity
-- ---------------------------------------------------------------------
create table if not exists public.progress_metrics (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  course_id     uuid references public.courses(id) on delete set null,
  metric        text not null,     -- gpa | study_minutes | retention | completion_rate ...
  value         numeric,
  captured_at   timestamptz not null default now(),
  metadata      jsonb not null default '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- Reminders
-- ---------------------------------------------------------------------
create table if not exists public.reminders (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  assignment_id uuid references public.assignments(id) on delete cascade,
  title         text not null,
  body          text,
  remind_at     timestamptz not null,
  sent          boolean not null default false,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Integrations (Phase 2 scaffold) — external LMS connections + sync state
-- Credentials should be stored encrypted; keep only references/tokens here.
-- ---------------------------------------------------------------------
create table if not exists public.integrations (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  provider      integration_provider not null,
  display_name  text,
  config        jsonb not null default '{}'::jsonb,   -- non-secret config
  secret_ref    text,                                  -- pointer to secret store
  status        sync_status not null default 'idle',
  last_synced_at timestamptz,
  last_error    text,
  enabled       boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (user_id, provider)
);

-- ---------------------------------------------------------------------
-- Agent conversations & messages (grounded chat history)
-- ---------------------------------------------------------------------
create table if not exists public.conversations (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  agent         agent_role not null default 'general',
  title         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists public.messages (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  role            message_role not null,
  content         text not null,
  -- retrieval provenance: what memory grounded this answer
  context_used    jsonb not null default '{}'::jsonb,
  tokens          integer,
  created_at      timestamptz not null default now()
);
