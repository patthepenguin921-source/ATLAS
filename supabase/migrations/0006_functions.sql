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
