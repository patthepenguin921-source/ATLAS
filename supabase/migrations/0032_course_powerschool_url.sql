-- =====================================================================
-- ATLAS — 0032 · Per-course PowerSchool assignments link override
--
-- PowerSchool's own course-list page links to each course's assignments
-- detail page (scores.html?frn=...), and the sync scrapes that link
-- automatically -- but a real account showed this auto-detection can land
-- on the wrong table (or a non-gradebook widget entirely) for some
-- courses. Letting a student paste the exact scores.html URL PowerSchool
-- itself shows them for a course gives a reliable manual fallback: when
-- set, PowerSchoolProvider.sync() fetches assignments from this URL
-- instead of whatever it scraped, bypassing the auto-detection entirely
-- for that course.
-- =====================================================================

alter table public.courses add column if not exists powerschool_url text;
