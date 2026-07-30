/**
 * Formats a stored date/timestamp as the calendar day it represents,
 * without letting the viewer's local timezone shift it.
 *
 * Assignment due dates and "at a glance" class-schedule days come from the
 * backend as a bare calendar date stored at midnight UTC (see
 * `app.services.schedule_extraction`, which only ever resolves a "day", not
 * a time of day). `new Date(iso).toLocaleDateString()` reinterprets that UTC
 * instant in the browser's own timezone, which lands on the *previous* day
 * for any timezone behind UTC -- e.g. midnight UTC on 2026-08-21 renders as
 * "Aug 20" in America/New_York. Extracting the Y/M/D digits directly and
 * building a fresh local `Date` from them sidesteps that conversion
 * entirely, so the displayed day always matches what was actually stored.
 */
export function formatCalendarDate(
  iso: string,
  options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" },
): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, options);
}
