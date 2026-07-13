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
