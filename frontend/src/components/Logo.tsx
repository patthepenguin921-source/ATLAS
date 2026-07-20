// The "Summit" mark — two mountain peaks with a marker at the top, after the
// Atlas Mountains and the goal-reached metaphor. Stroke color comes from
// `currentColor` (set it via a text-* class on the wrapper); `bg` fills the
// back peak so it reads as a separate shape instead of a tangle of
// overlapping strokes — pass whatever color sits directly behind the mark.
export function LogoMark({
  className = "w-5 h-5",
  bg = "none",
}: {
  className?: string;
  bg?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" fill="none" className={className} aria-hidden="true">
      <polygon
        points="8,50 24,22 40,50"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <polygon
        points="26,50 42,16 58,50"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinejoin="round"
        strokeLinecap="round"
        fill={bg}
      />
      <circle cx="42" cy="12" r="3" fill="#7dd3fc" />
    </svg>
  );
}
