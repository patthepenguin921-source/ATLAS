-- ---------------------------------------------------------------------
-- Fold assignments.weight into the course grade rollup.
--
-- assignments.weight has existed since 0002_core_tables.sql ("category
-- weight if known") but nothing ever wrote to it, and recompute_course_grade
-- only ever read grades.weight. That leaves a per-assignment minor(0.3)/
-- major(0.7) weight the student sets (see app.agents.tools' add_assignment
-- tool and the manual Add Assignment form) with nowhere to actually affect
-- current_grade/GPA. grades.weight -- set by an LMS sync, on the rare
-- occasion one ever populates it -- stays the more authoritative source
-- when both are present; assignments.weight is only a fallback.
-- ---------------------------------------------------------------------
create or replace function public.recompute_course_grade(p_course_id uuid)
returns numeric language plpgsql as $$
declare
  pct numeric;
begin
  select round(
           sum(g.percentage * coalesce(g.weight, a.weight, 1))
           / nullif(sum(coalesce(g.weight, a.weight, 1)), 0), 3)
    into pct
  from public.grades g
  left join public.assignments a on a.id = g.assignment_id
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
