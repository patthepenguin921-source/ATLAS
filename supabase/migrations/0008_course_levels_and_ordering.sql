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
