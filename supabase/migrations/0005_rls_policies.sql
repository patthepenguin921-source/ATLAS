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
