-- =====================================================================
-- ATLAS — 0013 · Continuous GPA scale
--
-- Replaces the stepped percentage_to_gpa conversion (flat 4.0 for any
-- grade >= 93, flat 3.7 for 90-92, ...) with a continuous scale that
-- matches the student's official transcript tracking: base points slide
-- with the exact grade instead of jumping in bands.
--
--   base    = 4.0 - (100 - pct) * 0.1
--   bonus   = +1.0 regular / +1.5 honors / +2.0 ap|dual_enrollment|ib
--             (any HN prep lab overrides to +1.5, same as before)
--   result  = base + bonus, floored at 0
--
-- Signature is unchanged, so predicted_gpa() picks this up automatically.
-- =====================================================================

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
      (4.0 - (100 - pct) * 0.1)
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
