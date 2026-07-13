"use client";

import { useEffect } from "react";

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

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

/** List-shaped loading placeholder used while a tab's data is in flight. */
export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="card flex items-center justify-between">
          <div className="space-y-2 w-1/2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-6 w-16" />
        </div>
      ))}
    </div>
  );
}

const RISK_TONE: Record<string, string> = {
  low: "text-atlas-good border-atlas-good/40 bg-atlas-good/10",
  medium: "text-atlas-warn border-atlas-warn/40 bg-atlas-warn/10",
  high: "text-atlas-bad border-atlas-bad/40 bg-atlas-bad/10",
  extreme: "text-atlas-bad border-atlas-bad/60 bg-atlas-bad/20 font-semibold",
};

export function RiskBadge({ level }: { level?: string | null }) {
  const key = (level ?? "low").toLowerCase();
  return (
    <span className={`pill capitalize ${RISK_TONE[key] ?? RISK_TONE.low}`}>
      {key} risk
    </span>
  );
}

/** Lightweight centered modal with a backdrop. Closes on backdrop click / Esc. */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-lg animate-fade-in shadow-soft"
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-start justify-between gap-4 mb-3">
            <h3 className="text-lg font-semibold">{title}</h3>
            <button
              onClick={onClose}
              className="text-atlas-muted hover:text-atlas-text text-xl leading-none"
              aria-label="Close"
            >
              ×
            </button>
          </div>
        )}
        <div>{children}</div>
        {footer && (
          <div className="mt-5 flex items-center justify-end gap-2">{footer}</div>
        )}
      </div>
    </div>
  );
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
    accent: "text-atlas-accent border-atlas-accent/40",
  }[tone];
  return <span className={`pill ${c}`}>{children}</span>;
}
