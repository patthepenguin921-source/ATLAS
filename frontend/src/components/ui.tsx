"use client";

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const toneColor = {
    default: "text-atlas-text",
    good: "text-atlas-good",
    warn: "text-atlas-warn",
    bad: "text-atlas-bad",
  }[tone];
  return (
    <div className="card">
      <div className="text-xs text-atlas-muted">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${toneColor}`}>{value}</div>
      {hint && <div className="text-xs text-atlas-muted mt-1">{hint}</div>}
    </div>
  );
}

export function Section({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-atlas-muted uppercase tracking-wide">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="card text-sm text-atlas-muted text-center py-8">{children}</div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="text-sm text-atlas-muted py-6">{label}</div>;
}

export function gradeTone(pct?: number | null): "good" | "warn" | "bad" | "default" {
  if (pct == null) return "default";
  if (pct >= 90) return "good";
  if (pct >= 80) return "warn";
  return "bad";
}

export function Badge({
  children,
  tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "good" | "warn" | "bad" | "accent";
}) {
  const c = {
    default: "text-atlas-muted",
    good: "text-atlas-good border-atlas-good/40",
    warn: "text-atlas-warn border-atlas-warn/40",
    bad: "text-atlas-bad border-atlas-bad/40",
    accent: "text-atlas-accent2 border-atlas-accent2/40",
  }[tone];
  return <span className={`pill ${c}`}>{children}</span>;
}
