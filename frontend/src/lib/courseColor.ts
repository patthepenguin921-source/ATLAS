// Small, distinct, deterministic palette for courses that haven't set their
// own `color` -- hashed from the course id so the same course always lands
// on the same swatch across a session without needing to persist anything.
// Shared by the calendar (per-day chips) and the course color picker (so the
// picker's swatches match what an unset course would fall back to).
export const FALLBACK_PALETTE = [
  "#6a8bff", "#f2994a", "#27ae60", "#eb5757", "#9b51e0",
  "#2d9cdb", "#f2c94c", "#bb6bd9", "#219653", "#56ccf2",
];

export function courseColor(courseId: string, explicit?: string | null): string {
  if (explicit) return explicit;
  let hash = 0;
  for (let i = 0; i < courseId.length; i++) hash = (hash * 31 + courseId.charCodeAt(i)) >>> 0;
  return FALLBACK_PALETTE[hash % FALLBACK_PALETTE.length];
}
