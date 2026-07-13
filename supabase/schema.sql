-- ============================================================
-- ATLAS · FULL SCHEMA (auto-generated concatenation of migrations)
-- Paste this whole file into Supabase Dashboard → SQL Editor → Run.
-- Idempotent: safe to re-run.
-- ============================================================

-- >>>>>>>>>> 0001_extensions_and_types.sql <<<<<<<<<<
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

-- >>>>>>>>>> 0002_core_tables.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0002 · Core Tables (structured factual memory)
-- Every row is owned by a user (auth.users). RLS enforced in 0005.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Profile — extends Supabase auth.users with academic identity
-- ---------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  full_name     text,
  school        text,
  grade_level   text,               -- e.g. "11th", "Sophomore"
  gpa_goal      numeric(4,3),       -- target GPA
  timezone      text default 'America/New_York',
  preferences   jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Terms — grading periods (semester / quarter)
-- ---------------------------------------------------------------------
create table if not exists public.terms (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,               -- "Fall 2026", "Q1"
  start_date  date,
  end_date    date,
  is_current  boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Teachers
-- ---------------------------------------------------------------------
create table if not exists public.teachers (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  name           text not null,
  email          text,
  subject        text,
  -- Learned grading tendencies (updated by the Analyst agent)
  grading_notes  text,
  tendencies     jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Courses
-- ---------------------------------------------------------------------
create table if not exists public.courses (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  term_id      uuid references public.terms(id) on delete set null,
  teacher_id   uuid references public.teachers(id) on delete set null,
  name         text not null,
  code         text,                       -- "AP CALC BC"
  subject      text,                       -- "Mathematics"
  is_ap        boolean not null default false,
  is_honors    boolean not null default false,
  credit_hours numeric(3,1) default 1.0,
  color        text,                       -- UI hint
  period       text,                       -- schedule slot
  room         text,
  -- rolling grade snapshot maintained by triggers / analytics
  current_grade      numeric(6,3),
  current_letter     text,
  external_id        text,                 -- id in Schoology/PowerSchool/etc
  external_source    integration_provider,
  metadata     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Assignments — the atomic unit of academic work
-- (homework, quizzes, tests, projects, essays … unified)
-- ---------------------------------------------------------------------
create table if not exists public.assignments (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  course_id      uuid references public.courses(id) on delete cascade,
  term_id        uuid references public.terms(id) on delete set null,
  title          text not null,
  description    text,
  category       assignment_category not null default 'homework',
  status         assignment_status not null default 'not_started',
  assigned_date  date,
  due_date       timestamptz,
  submitted_at   timestamptz,
  -- weighting & difficulty metadata
  points_possible        numeric(8,2),
  weight                 numeric(6,3),       -- category weight if known
  difficulty             smallint,           -- 1..5, learned/estimated
  estimated_minutes      integer,            -- predicted completion time
  actual_minutes         integer,            -- measured
  -- learning linkage
  learning_objectives    text[],
  tags                   text[],
  external_id            text,
  external_source        integration_provider,
  metadata               jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Grades — recorded scores (an assignment can have one authoritative grade;
-- standalone grades are allowed for imported category scores)
-- ---------------------------------------------------------------------
create table if not exists public.grades (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  course_id       uuid references public.courses(id) on delete cascade,
  assignment_id   uuid references public.assignments(id) on delete cascade,
  score           numeric(8,2),
  points_possible numeric(8,2),
  percentage      numeric(6,3),        -- computed convenience
  letter          text,
  weight          numeric(6,3),
  graded_at       timestamptz,
  teacher_comment text,
  rubric          jsonb,               -- per-criterion breakdown
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Calendar events (classes, due dates, exams, personal)
-- ---------------------------------------------------------------------
create table if not exists public.calendar_events (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  course_id    uuid references public.courses(id) on delete set null,
  assignment_id uuid references public.assignments(id) on delete set null,
  title        text not null,
  description  text,
  location     text,
  starts_at    timestamptz not null,
  ends_at      timestamptz,
  all_day      boolean not null default false,
  kind         text default 'event',   -- event | class | due | exam | reminder
  external_id  text,
  metadata     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Study sessions — the log of actual learning activity
-- ---------------------------------------------------------------------
create table if not exists public.study_sessions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  course_id      uuid references public.courses(id) on delete set null,
  assignment_id  uuid references public.assignments(id) on delete set null,
  started_at     timestamptz not null default now(),
  ended_at       timestamptz,
  duration_minutes integer,
  focus_rating   smallint,            -- 1..5 self-reported focus
  technique      text,                -- active_recall | spaced_rep | practice | reading
  notes          text,
  concept_ids    uuid[],
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Announcements (teacher / course notices — often auto-ingested)
-- ---------------------------------------------------------------------
create table if not exists public.announcements (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  course_id    uuid references public.courses(id) on delete set null,
  teacher_id   uuid references public.teachers(id) on delete set null,
  title        text,
  body         text,
  posted_at    timestamptz,
  external_id  text,
  external_source integration_provider,
  metadata     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- >>>>>>>>>> 0003_knowledge_and_documents.sql <<<<<<<<<<
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

-- >>>>>>>>>> 0004_indexes_and_triggers.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0004 · Indexes & Triggers
-- =====================================================================

-- ---------- Ownership / lookup indexes ----------
create index if not exists idx_courses_user            on public.courses(user_id);
create index if not exists idx_courses_term            on public.courses(term_id);
create index if not exists idx_assignments_user        on public.assignments(user_id);
create index if not exists idx_assignments_course      on public.assignments(course_id);
create index if not exists idx_assignments_due         on public.assignments(user_id, due_date);
create index if not exists idx_assignments_status      on public.assignments(user_id, status);
create index if not exists idx_grades_user             on public.grades(user_id);
create index if not exists idx_grades_course           on public.grades(course_id);
create index if not exists idx_grades_assignment       on public.grades(assignment_id);
create index if not exists idx_events_user_time        on public.calendar_events(user_id, starts_at);
create index if not exists idx_study_user_time         on public.study_sessions(user_id, started_at);
create index if not exists idx_documents_user          on public.documents(user_id);
create index if not exists idx_documents_course        on public.documents(course_id);
create index if not exists idx_chunks_user             on public.document_chunks(user_id);
create index if not exists idx_chunks_document         on public.document_chunks(document_id);
create index if not exists idx_concepts_user           on public.concepts(user_id);
create index if not exists idx_edges_from              on public.concept_edges(from_concept);
create index if not exists idx_edges_to                on public.concept_edges(to_concept);
create index if not exists idx_knowledge_user          on public.student_knowledge(user_id);
create index if not exists idx_knowledge_review        on public.student_knowledge(user_id, next_review_at);
create index if not exists idx_mistakes_user           on public.mistakes(user_id);
create index if not exists idx_metrics_user_metric     on public.progress_metrics(user_id, metric, captured_at);
create index if not exists idx_messages_conversation   on public.messages(conversation_id, created_at);
create index if not exists idx_announcements_user      on public.announcements(user_id);

-- ---------- Full-text-ish trigram indexes ----------
create index if not exists idx_documents_text_trgm     on public.documents using gin (extracted_text gin_trgm_ops);
create index if not exists idx_assignments_title_trgm  on public.assignments using gin (title gin_trgm_ops);

-- ---------- Vector (ANN) indexes ----------
-- IVFFlat needs data present to build well; these are safe on empty tables
-- and Postgres will use them as rows arrive. Tune `lists` as the corpus grows.
create index if not exists idx_chunks_embedding on public.document_chunks
  using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists idx_concepts_embedding on public.concepts
  using ivfflat (embedding vector_cosine_ops) with (lists = 50);

-- ---------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array[
    'profiles','terms','teachers','courses','assignments','grades',
    'calendar_events','study_sessions','announcements','documents',
    'concepts','student_knowledge','daily_plans','weekly_reviews',
    'integrations','conversations'
  ] loop
    execute format(
      'drop trigger if exists trg_%1$s_updated on public.%1$s;
       create trigger trg_%1$s_updated before update on public.%1$s
       for each row execute function public.set_updated_at();', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- Derive grade percentage automatically
-- ---------------------------------------------------------------------
create or replace function public.compute_grade_percentage()
returns trigger language plpgsql as $$
begin
  if new.points_possible is not null and new.points_possible > 0
     and new.score is not null then
    new.percentage = round((new.score / new.points_possible) * 100, 3);
  end if;
  return new;
end $$;

drop trigger if exists trg_grades_percentage on public.grades;
create trigger trg_grades_percentage before insert or update on public.grades
  for each row execute function public.compute_grade_percentage();

-- ---------------------------------------------------------------------
-- Auto-create a profile row when a new auth user signs up
-- ---------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', new.email))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- >>>>>>>>>> 0005_rls_policies.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0005 · Row Level Security
-- Each user sees only their own rows. The backend service-role key
-- bypasses RLS by design; the browser (anon key) is fully constrained.
-- =====================================================================

-- profiles keyed by id (= auth.uid())
alter table public.profiles enable row level security;
drop policy if exists profiles_self on public.profiles;
create policy profiles_self on public.profiles
  using (id = auth.uid()) with check (id = auth.uid());

-- All other tables keyed by user_id
do $$
declare t text;
begin
  foreach t in array array[
    'terms','teachers','courses','assignments','grades','calendar_events',
    'study_sessions','announcements','documents','document_chunks','concepts',
    'concept_edges','assignment_concepts','document_concepts','student_knowledge',
    'mistakes','daily_plans','weekly_reviews','progress_metrics','reminders',
    'integrations','conversations','messages'
  ] loop
    execute format('alter table public.%1$s enable row level security;', t);
    execute format('drop policy if exists %1$s_owner on public.%1$s;', t);
    execute format(
      'create policy %1$s_owner on public.%1$s
         using (user_id = auth.uid())
         with check (user_id = auth.uid());', t);
  end loop;
end $$;

-- >>>>>>>>>> 0006_functions.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0006 · RPC Functions (semantic search, GPA, grade rollups)
-- Callable from the backend via PostgREST /rpc/*.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Semantic search over document chunks (cosine similarity)
-- ---------------------------------------------------------------------
create or replace function public.match_document_chunks(
  query_embedding vector(1024),
  p_user_id uuid,
  match_count int default 8,
  similarity_threshold float default 0.0,
  p_course_id uuid default null
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index int,
  content text,
  similarity float,
  document_title text
)
language sql stable as $$
  select c.id, c.document_id, c.chunk_index, c.content,
         1 - (c.embedding <=> query_embedding) as similarity,
         d.title as document_title
  from public.document_chunks c
  join public.documents d on d.id = c.document_id
  where c.user_id = p_user_id
    and c.embedding is not null
    and (p_course_id is null or d.course_id = p_course_id)
    and 1 - (c.embedding <=> query_embedding) >= similarity_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- ---------------------------------------------------------------------
-- Semantic search over concepts (knowledge-graph entry points)
-- ---------------------------------------------------------------------
create or replace function public.match_concepts(
  query_embedding vector(1024),
  p_user_id uuid,
  match_count int default 6,
  similarity_threshold float default 0.0
)
returns table (id uuid, name text, description text, similarity float)
language sql stable as $$
  select c.id, c.name, c.description,
         1 - (c.embedding <=> query_embedding) as similarity
  from public.concepts c
  where c.user_id = p_user_id
    and c.embedding is not null
    and 1 - (c.embedding <=> query_embedding) >= similarity_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- ---------------------------------------------------------------------
-- Letter grade / GPA points helper (standard US 4.0 scale)
-- ---------------------------------------------------------------------
create or replace function public.percentage_to_gpa(pct numeric, is_ap boolean default false, is_honors boolean default false)
returns numeric language sql immutable as $$
  select case
    when pct is null then null
    else greatest(0,
      case
        when pct >= 93 then 4.0
        when pct >= 90 then 3.7
        when pct >= 87 then 3.3
        when pct >= 83 then 3.0
        when pct >= 80 then 2.7
        when pct >= 77 then 2.3
        when pct >= 73 then 2.0
        when pct >= 70 then 1.7
        when pct >= 67 then 1.3
        when pct >= 65 then 1.0
        else 0.0
      end
      + case when is_ap then 1.0 when is_honors then 0.5 else 0.0 end
    )
  end;
$$;

-- ---------------------------------------------------------------------
-- Recompute a course's rolling grade from its graded assignments.
-- Uses category weights when present, else a simple points-based average.
-- ---------------------------------------------------------------------
create or replace function public.recompute_course_grade(p_course_id uuid)
returns numeric language plpgsql as $$
declare
  pct numeric;
begin
  -- Weighted average of grade percentages, weight defaulting to 1.
  select round(
           sum(g.percentage * coalesce(g.weight, 1))
           / nullif(sum(coalesce(g.weight, 1)), 0), 3)
    into pct
  from public.grades g
  where g.course_id = p_course_id
    and g.percentage is not null;

  update public.courses
     set current_grade = pct,
         current_letter = case
           when pct is null then null
           when pct >= 93 then 'A'   when pct >= 90 then 'A-'
           when pct >= 87 then 'B+'  when pct >= 83 then 'B'
           when pct >= 80 then 'B-'  when pct >= 77 then 'C+'
           when pct >= 73 then 'C'   when pct >= 70 then 'C-'
           when pct >= 67 then 'D+'  when pct >= 65 then 'D'
           else 'F' end
   where id = p_course_id;

  return pct;
end $$;

-- Keep course grade fresh as grades change
create or replace function public.trg_recompute_course_grade()
returns trigger language plpgsql as $$
begin
  perform public.recompute_course_grade(coalesce(new.course_id, old.course_id));
  return coalesce(new, old);
end $$;

drop trigger if exists trg_grades_rollup on public.grades;
create trigger trg_grades_rollup
  after insert or update or delete on public.grades
  for each row execute function public.trg_recompute_course_grade();

-- ---------------------------------------------------------------------
-- Predicted GPA across a user's current-term courses
-- ---------------------------------------------------------------------
create or replace function public.predicted_gpa(p_user_id uuid, p_weighted boolean default true)
returns numeric language sql stable as $$
  select round(
    sum(public.percentage_to_gpa(c.current_grade,
          p_weighted and c.is_ap, p_weighted and c.is_honors) * coalesce(c.credit_hours,1))
    / nullif(sum(coalesce(c.credit_hours,1)), 0), 3)
  from public.courses c
  where c.user_id = p_user_id
    and c.current_grade is not null;
$$;

-- >>>>>>>>>> 0007_storage.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0007 · Storage bucket for uploaded documents
-- Private bucket; access is per-user by path prefix (userId/...).
-- =====================================================================

insert into storage.buckets (id, name, public)
values ('atlas-documents', 'atlas-documents', false)
on conflict (id) do nothing;

-- Users may only touch objects under a folder named after their uid.
-- The backend (service role) bypasses these and can manage all objects.

drop policy if exists "atlas docs read own" on storage.objects;
create policy "atlas docs read own" on storage.objects
  for select using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs insert own" on storage.objects;
create policy "atlas docs insert own" on storage.objects
  for insert with check (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs update own" on storage.objects;
create policy "atlas docs update own" on storage.objects
  for update using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs delete own" on storage.objects;
create policy "atlas docs delete own" on storage.objects
  for delete using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- >>>>>>>>>> 0008_course_levels_and_ordering.sql <<<<<<<<<<
-- =====================================================================
-- ATLAS — 0008 · Course levels, prep labs, and manual course ordering
--
-- Replaces the old is_ap / is_honors booleans with a single course_level
-- enum (regular / honors / ap / dual_enrollment / ib) plus two prep-lab
-- add-on flags that can be attached to any course without splitting it
-- into a second row. Also adds sort_order so courses can be manually
-- reordered on the Courses page.
--
-- Weighted GPA scale (see percentage_to_gpa below):
--   regular                          -> +1.0 bonus (5.0 max)
--   honors                           -> +1.5 bonus (5.5 max)
--   ap / dual_enrollment / ib        -> +2.0 bonus (6.0 max)
--   any course with an HN prep lab   -> +1.5 bonus (5.5 max), overriding
--                                        its base course_level bonus
--   AP prep lab does not change the bonus (stays at the course's own
--   course_level weight — informational/scheduling flag only)
-- =====================================================================

do $$ begin
  create type course_level as enum ('regular','honors','ap','dual_enrollment','ib');
exception when duplicate_object then null; end $$;

alter table public.courses
  add column if not exists course_level    course_level not null default 'regular',
  add column if not exists has_hn_prep_lab boolean not null default false,
  add column if not exists has_ap_prep_lab boolean not null default false,
  add column if not exists sort_order      integer;

-- Backfill course_level from the booleans being retired
update public.courses
   set course_level = case
     when is_ap then 'ap'::course_level
     when is_honors then 'honors'::course_level
     else 'regular'::course_level
   end
 where course_level = 'regular';

-- Backfill sort_order per-user from creation order
with ranked as (
  select id, row_number() over (partition by user_id order by created_at asc, id asc) - 1 as rn
  from public.courses
  where sort_order is null
)
update public.courses c
   set sort_order = ranked.rn
  from ranked
 where c.id = ranked.id;

alter table public.courses
  alter column sort_order set default 0,
  alter column sort_order set not null;

alter table public.courses
  drop column if exists is_ap,
  drop column if exists is_honors;

create index if not exists idx_courses_sort_order on public.courses(user_id, sort_order);

-- ---------------------------------------------------------------------
-- Letter grade / GPA points helper, redefined for course_level + prep labs
-- ---------------------------------------------------------------------
create or replace function public.percentage_to_gpa(
  pct numeric,
  p_course_level course_level default 'regular',
  p_has_hn_prep_lab boolean default false,
  p_weighted boolean default true
)
returns numeric language sql immutable as $$
  select case
    when pct is null then null
    else greatest(0,
      case
        when pct >= 93 then 4.0
        when pct >= 90 then 3.7
        when pct >= 87 then 3.3
        when pct >= 83 then 3.0
        when pct >= 80 then 2.7
        when pct >= 77 then 2.3
        when pct >= 73 then 2.0
        when pct >= 70 then 1.7
        when pct >= 67 then 1.3
        when pct >= 65 then 1.0
        else 0.0
      end
      + case
          when not p_weighted then 0.0
          when p_has_hn_prep_lab then 1.5
          when p_course_level in ('ap','dual_enrollment','ib') then 2.0
          when p_course_level = 'honors' then 1.5
          else 1.0
        end
    )
  end;
$$;

-- ---------------------------------------------------------------------
-- Predicted GPA across a user's current-term courses
-- ---------------------------------------------------------------------
create or replace function public.predicted_gpa(p_user_id uuid, p_weighted boolean default true)
returns numeric language sql stable as $$
  select round(
    sum(public.percentage_to_gpa(c.current_grade, c.course_level, c.has_hn_prep_lab, p_weighted)
        * coalesce(c.credit_hours,1))
    / nullif(sum(coalesce(c.credit_hours,1)), 0), 3)
  from public.courses c
  where c.user_id = p_user_id
    and c.current_grade is not null;
$$;

-- >>>>>>>>>> 0009_course_semesters.sql <<<<<<<<<<
-- Course semesters & linked semester courses: a class can be split into two
-- linked rows (e.g. HN Prep Lab S1 @ 5.5, AP S2 @ 6.0) tracked independently.

alter table public.courses
  add column if not exists semester         text not null default 'full_year',
  add column if not exists linked_course_id uuid references public.courses(id) on delete set null;

do $$ begin
  alter table public.courses
    add constraint courses_semester_check
    check (semester in ('full_year', 's1', 's2'));
exception when duplicate_object then null; end $$;

create index if not exists idx_courses_linked
  on public.courses(user_id, linked_course_id);

