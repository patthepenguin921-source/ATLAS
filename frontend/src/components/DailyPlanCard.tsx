"use client";

import { formatCalendarDate } from "@/lib/date";

export function dayLabel(dateStr: string, planDate: string): string {
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date(planDate + "T00:00:00");
  const diffDays = Math.round((d.getTime() - today.getTime()) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Tomorrow";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export function groupTasksByDate(tasks: any[]): [string, any[]][] {
  const groups = new Map<string, any[]>();
  for (const t of tasks ?? []) {
    const key = t.date ?? "unscheduled";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
}

/** Renders a Planner-generated daily plan (see `POST /agents/planner/daily-plan`)
 *  -- shared by the dashboard and the Knowledge/Study page so "what needs to
 *  happen today" reads identically wherever it shows up. `onRegenerate` is
 *  optional -- the dashboard drives regeneration from its own top-level
 *  "Generate today's plan" button instead, so it doesn't pass one. */
export function DailyPlanCard({
  plan,
  onRegenerate,
  regenerating,
}: {
  plan: any;
  onRegenerate?: () => void;
  regenerating?: boolean;
}) {
  if (!plan) return null;
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm">{plan.summary}</p>
        {onRegenerate && (
          <button className="btn-ghost text-xs shrink-0" onClick={onRegenerate} disabled={regenerating}>
            {regenerating ? "Replanning…" : "Regenerate"}
          </button>
        )}
      </div>
      {!!plan.priorities?.length && (
        <ul className="text-sm list-disc list-inside mt-3 space-y-0.5">
          {plan.priorities.map((p: string, i: number) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      <div className="mt-4 space-y-4">
        {groupTasksByDate(plan.blocks).map(([date, tasks]) => (
          <div key={date}>
            <div className="text-xs font-semibold text-atlas-accent2 uppercase tracking-wide mb-1.5">
              {date === "unscheduled" ? "Whenever you can" : dayLabel(date, plan.plan_date)}
            </div>
            <div className="space-y-2">
              {tasks.map((t: any, i: number) => (
                <div key={i} className="flex items-start justify-between gap-3 text-sm">
                  <div className="min-w-0">
                    <span>{t.task}</span>
                    {t.part && <span className="text-atlas-muted"> · {t.part}</span>}
                    {(t.course || t.why) && (
                      <div className="text-xs text-atlas-muted">
                        {[t.course, t.why].filter(Boolean).join(" — ")}
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-atlas-muted whitespace-nowrap text-right">
                    {t.due_date && t.due_date !== date && (
                      <div>due {formatCalendarDate(t.due_date)}</div>
                    )}
                    {t.estimated_minutes ? <div>{t.estimated_minutes}m</div> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {plan.motivational_note && (
        <p className="text-xs text-atlas-muted mt-4 italic">{plan.motivational_note}</p>
      )}
    </div>
  );
}
