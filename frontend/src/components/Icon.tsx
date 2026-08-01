/** Shared monoline SVG icon set — keeps the app's icon language consistent
 *  (matches the style already used for nav icons in Sidebar.tsx) instead of
 *  mixing in emoji or ad-hoc glyphs (📎, ×, ✎, ⋯, ▸ …) as icons. */

const PATHS = {
  close: ["M18 6 6 18", "M6 6l12 12"],
  plus: ["M12 5v14", "M5 12h14"],
  paperclip: [
    "M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48",
  ],
  pin: [
    "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z",
    "M12 17v5",
  ],
  folder: [
    "M4 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z",
  ],
  document: ["M14 3v5h5", "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"],
  assignment: [
    "M9 5h6M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2M9 5H7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2",
    "M9 13l2 2 4-4",
  ],
  message: ["M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-4 4V5Z"],
  copy: [
    "M8 8h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z",
    "M5 16H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1",
  ],
  thumbsUp: [
    "M7 10v11",
    "M2 12v7a2 2 0 0 0 2 2h12.5a2 2 0 0 0 2-1.6l1.4-7A2 2 0 0 0 18 10h-5.5l1-4.5a1.5 1.5 0 0 0-2.6-1.3L7 10H2Z",
  ],
  thumbsDown: [
    "M17 14V3",
    "M22 12V5a2 2 0 0 0-2-2H7.5a2 2 0 0 0-2 1.6l-1.4 7A2 2 0 0 0 6 14h5.5l-1 4.5a1.5 1.5 0 0 0 2.6 1.3L17 14h5Z",
  ],
  refresh: [
    "M21 12a9 9 0 0 1-15.3 6.4",
    "M3 12a9 9 0 0 1 15.3-6.4",
    "M3 4v5h5",
    "M21 20v-5h-5",
  ],
  check: ["M5 12l5 5L20 7"],
  chevronRight: ["M9 6l6 6-6 6"],
  chevronDown: ["M6 9l6 6 6-6"],
  chevronLeft: ["M15 6l-6 6 6 6"],
  warning: ["M12 3 2 21h20L12 3Z", "M12 10v4", "M12 17h.01"],
  pencil: ["M12 20h9", "M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"],
  expand: ["M15 3h6v6", "M9 21H3v-6", "M21 3l-7 7", "M3 21l7-7"],
} as const;

export type IconName = keyof typeof PATHS | "moreVertical";

export function Icon({ name, className = "w-4 h-4" }: { name: IconName; className?: string }) {
  if (name === "moreVertical") {
    return (
      <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
        <circle cx="12" cy="5" r="1.6" />
        <circle cx="12" cy="12" r="1.6" />
        <circle cx="12" cy="19" r="1.6" />
      </svg>
    );
  }
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name].map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
