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
