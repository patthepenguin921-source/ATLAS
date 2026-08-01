-- =====================================================================
-- ATLAS — 0026 · Study availability + flashcards
--
-- Two additions behind the Knowledge tab's rework into a real study hub:
--
-- 1. `study_availability` -- how many minutes a student actually has to
--    study on a given day of the week. A flat "180 minutes every day"
--    assumption (the Planner's old hardcoded default) doesn't survive
--    contact with a kid who has practice/work some days and nothing
--    others -- one row per weekday (0=Monday..6=Sunday, matching Python's
--    `date.weekday()`) lets the Planner read the real number for whatever
--    day it's planning instead of guessing.
--
-- 2. `flashcards` -- generated from a single document or every document in
--    a unit/folder (see app.services.flashcards), reviewed with the same
--    SM-2 spaced-repetition math as the concept knowledge model
--    (`student_knowledge` in 0003) but kept as its own table: a generated
--    term/definition pair doesn't necessarily correspond to an existing
--    `concepts` row, and forcing that match would mean silently dropping
--    cards that don't happen to line up with the concept graph.
-- =====================================================================

create table if not exists public.study_availability (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  day_of_week smallint not null check (day_of_week between 0 and 6),
  minutes     integer not null default 0 check (minutes >= 0),
  updated_at  timestamptz not null default now(),
  unique (user_id, day_of_week)
);

alter table public.study_availability enable row level security;
drop policy if exists study_availability_owner on public.study_availability;
create policy study_availability_owner on public.study_availability
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

drop trigger if exists trg_study_availability_updated on public.study_availability;
create trigger trg_study_availability_updated before update on public.study_availability
  for each row execute function public.set_updated_at();

create table if not exists public.flashcards (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  course_id       uuid references public.courses(id) on delete cascade,
  folder_id       uuid references public.folders(id) on delete cascade,
  document_id     uuid references public.documents(id) on delete cascade,
  front           text not null,
  back            text not null,
  -- what this card was generated from, for display ("From: Unit 3 Slideshow"
  -- or "From: AP Biology") -- kept even if the source document/folder is
  -- later deleted, since the card itself should still make sense on its own.
  source_label    text,
  -- SM-2 spaced-repetition state -- same fields/formula as
  -- `student_knowledge` (see app.services.knowledge_model.compute_sm2),
  -- just scoped to one card instead of one concept.
  ease_factor     real not null default 2.5,
  repetitions     integer not null default 0,
  interval_days   integer not null default 0,
  last_reviewed_at timestamptz,
  next_review_at  timestamptz not null default now(),
  created_at      timestamptz not null default now()
);

create index if not exists idx_flashcards_user on public.flashcards(user_id);
create index if not exists idx_flashcards_course on public.flashcards(user_id, course_id);
create index if not exists idx_flashcards_folder on public.flashcards(user_id, folder_id);
create index if not exists idx_flashcards_document on public.flashcards(user_id, document_id);
create index if not exists idx_flashcards_due on public.flashcards(user_id, next_review_at);

alter table public.flashcards enable row level security;
drop policy if exists flashcards_owner on public.flashcards;
create policy flashcards_owner on public.flashcards
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
