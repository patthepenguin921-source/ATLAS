"use client";

import { FALLBACK_PALETTE, courseColor } from "@/lib/courseColor";

/** A row of swatches for picking a course's calendar color, plus a "let
 *  Atlas pick" option that clears the explicit color and falls back to the
 *  deterministic per-course hash the calendar already uses for any course
 *  without one -- see `courseColor` in `@/lib/courseColor`. */
export function ColorPicker({
  courseId,
  value,
  onChange,
}: {
  courseId: string;
  value: string | null | undefined;
  onChange: (color: string | null) => void;
}) {
  const fallback = courseColor(courseId || "new");
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        title="Let Atlas pick"
        onClick={() => onChange(null)}
        className={`w-6 h-6 rounded-full border-2 transition-transform ${
          !value ? "border-atlas-text scale-110" : "border-transparent"
        }`}
        style={{ background: `repeating-conic-gradient(${fallback} 0% 25%, transparent 0% 50%)` }}
      />
      {FALLBACK_PALETTE.map((c) => (
        <button
          key={c}
          type="button"
          title={c}
          onClick={() => onChange(c)}
          className={`w-6 h-6 rounded-full border-2 transition-transform ${
            value === c ? "border-atlas-text scale-110" : "border-transparent"
          }`}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}
