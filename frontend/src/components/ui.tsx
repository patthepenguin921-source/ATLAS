"use client";

import { useEffect, useRef, useState } from "react";

/** Same layout as Stat, but the value is blurred until the user taps it —
 *  for numbers like GPA that someone might not want visible over your shoulder. */
export function RevealStat({
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
  const [revealed, setRevealed] = useState(false);
  const toneColor = {
    default: "text-atlas-text",
    good: "text-atlas-good",
    warn: "text-atlas-warn",
    bad: "text-atlas-bad",
  }[tone];
  return (
    <button
      type="button"
      onClick={() => setRevealed((r) => !r)}
      className="card text-left w-full"
      title={revealed ? "Tap to hide" : "Tap to reveal"}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-atlas-muted">{label}</div>
        <div className="text-[10px] text-atlas-muted">{revealed ? "hide" : "tap to reveal"}</div>
      </div>
      <div
        className={`text-2xl font-semibold mt-1 ${toneColor} transition-[filter] ${
          revealed ? "" : "blur-md select-none"
        }`}
      >
        {value}
      </div>
      {hint && <div className="text-xs text-atlas-muted mt-1">{hint}</div>}
    </button>
  );
}

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

/** Row of stat-card-shaped placeholders -- dashboard/analytics headline
 *  numbers (GPA, streaks, risk counts, …) while their data is in flight. */
export function SkeletonStats({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card">
          <Skeleton className="h-3 w-2/3 mb-2" />
          <Skeleton className="h-7 w-1/2" />
        </div>
      ))}
    </div>
  );
}

/** Grid of card-shaped placeholders -- course tiles and other grid layouts
 *  while their data is in flight. */
export function SkeletonGrid({ items = 6 }: { items?: number }) {
  return (
    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: items }).map((_, i) => (
        <div key={i} className="card space-y-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-2 w-full mt-3" />
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

const RING_COLOR: Record<"good" | "warn" | "bad" | "default", string> = {
  good: "#4ade80",
  warn: "#fbbf24",
  bad: "#f87171",
  default: "#9096a3",
};

/** A small at-a-glance donut for any 0-100 health/completion metric (sync
 *  freshness, grade, mastery, workload capacity, …) — the same shape used
 *  for Settings' sync health so a percentage reads as a glance, not a
 *  number you have to parse. `size` in px; center label defaults to the
 *  percent itself but can be overridden (e.g. a letter grade). */
export function Ring({
  percent,
  tone = "default",
  size = 44,
  label,
}: {
  percent: number;
  tone?: "good" | "warn" | "bad" | "default";
  size?: number;
  label?: React.ReactNode;
}) {
  const color = RING_COLOR[tone];
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      className="relative rounded-full shrink-0"
      style={{ width: size, height: size, background: `conic-gradient(${color} ${clamped * 3.6}deg, var(--ring-track, #252932) 0deg)` }}
    >
      <div
        className="absolute rounded-full bg-atlas-panel grid place-items-center font-bold tabular-nums"
        style={{ inset: Math.max(3, Math.round(size * 0.11)), color, fontSize: Math.max(8, Math.round(size * 0.22)) }}
      >
        {label ?? `${Math.round(clamped)}%`}
      </div>
    </div>
  );
}

/** Small "⋯" overflow menu for secondary row actions (edit, debug tools,
 *  rename, delete, …) — keeps a row's primary action + status visible and
 *  pushes everything else behind one click instead of a row of ghost
 *  buttons competing for attention. */
export function ActionMenu({
  items,
}: {
  items: { label: string; onClick: () => void; danger?: boolean; disabled?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        className="btn-ghost px-2.5"
        aria-label="More actions"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-52 rounded-xl border border-atlas-border bg-atlas-panel shadow-soft z-20 py-1 text-sm overflow-hidden">
          {items.map((it, i) => (
            <button
              key={i}
              type="button"
              disabled={it.disabled}
              className={`w-full text-left px-3 py-1.5 transition-colors hover:bg-atlas-panel2 disabled:opacity-40 disabled:cursor-not-allowed ${
                it.danger ? "text-atlas-bad" : "text-atlas-text"
              }`}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                it.onClick();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
