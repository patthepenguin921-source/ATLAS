/** Three staggered bouncing dots -- replaces a static "Thinking…" label
 *  with something that reads as live activity rather than a frozen state. */
export function ThinkingDots({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-atlas-muted">
      {label && <span>{label}</span>}
      <span className="flex items-center gap-0.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="w-1 h-1 rounded-full bg-atlas-muted animate-bounce"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </span>
    </div>
  );
}
