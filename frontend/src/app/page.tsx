"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Stat, RevealStat, Section, Empty, Loading, gradeTone, Badge, RiskBadge } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

function dayLabel(dateStr: string, planDate: string): string {
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date(planDate + "T00:00:00");
  const diffDays = Math.round((d.getTime() - today.getTime()) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Tomorrow";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

function groupTasksByDate(tasks: any[]): [string, any[]][] {
  const groups = new Map<string, any[]>();
  for (const t of tasks ?? []) {
    const key = t.date ?? "unscheduled";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  async function load() {
    try {
      setData(await apiGet("/dashboard"));
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function generatePlan() {
    setPlanning(true);
    try {
      await apiPost("/agents/planner/daily-plan", { available_minutes: 180 });
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setPlanning(false);
    }
  }

  const courseName = (id: string) =>
    data?.courses?.find((c: any) => c.id === id)?.name ?? "—";

  return (
    <AppShell
      title="Today"
      subtitle={data ? new Date(data.date).toDateString() : "Your morning briefing"}
      actions={
        <button className="btn-primary" onClick={generatePlan} disabled={planning}>
          {planning ? "Planning…" : "Generate today's plan"}
        </button>
      }
    >
      {error && (
        <div className="card border-atlas-bad/40 text-atlas-bad text-sm mb-6">
          {error} — is the backend running &amp; are you signed in?
        </div>
      )}
      {!data && !error && <Loading label="Assembling your briefing…" />}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <RevealStat
              label="GPA"
              value={data.predicted_gpa_unweighted ?? "—"}
              tone="good"
              hint={`weighted ${data.predicted_gpa_weighted ?? "—"}`}
            />
            <Stat label="Due soon" value={data.priorities_today?.length ?? 0} />
            <Stat
              label="Overdue / missing"
              value={data.overdue?.length ?? 0}
              tone={data.overdue?.length ? "bad" : "good"}
            />
            <Stat
              label="Est. workload"
              value={`${data.estimated_workload_minutes ?? 0}m`}
              hint="today"
            />
          </div>

          {data.daily_plan && (
            <Section title="What needs to happen">
              <div className="card">
                <p className="text-sm">{data.daily_plan.summary}</p>
                {!!data.daily_plan.priorities?.length && (
                  <ul className="text-sm list-disc list-inside mt-3 space-y-0.5">
                    {data.daily_plan.priorities.map((p: string, i: number) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                )}
                <div className="mt-4 space-y-4">
                  {groupTasksByDate(data.daily_plan.blocks).map(([date, tasks]) => (
                    <div key={date}>
                      <div className="text-xs font-semibold text-atlas-accent2 uppercase tracking-wide mb-1.5">
                        {date === "unscheduled" ? "Whenever you can" : dayLabel(date, data.daily_plan.plan_date)}
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
                                <div>due {new Date(t.due_date).toLocaleDateString()}</div>
                              )}
                              {t.estimated_minutes ? <div>{t.estimated_minutes}m</div> : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                {data.daily_plan.motivational_note && (
                  <p className="text-xs text-atlas-muted mt-4 italic">
                    {data.daily_plan.motivational_note}
                  </p>
                )}
              </div>
            </Section>
          )}

          <div className="grid md:grid-cols-2 gap-6">
            <Section title="Priorities">
              {data.priorities_today?.length ? (
                <div className="space-y-2">
                  {data.priorities_today.map((a: any) => (
                    <button key={a.id}
                      onClick={() => router.push("/assignments")}
                      className="card card-hover w-full text-left flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium">{a.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(a.course_id)} · {a.category}
                        </div>
                      </div>
                      <Badge tone="accent">
                        {a.due_date ? new Date(a.due_date).toLocaleDateString() : "—"}
                      </Badge>
                    </button>
                  ))}
                </div>
              ) : (
                <Empty>Nothing due soon. 🎉</Empty>
              )}
            </Section>

            <Section title="At risk">
              {data.at_risk?.length ? (
                <div className="space-y-2">
                  {data.at_risk.map((a: any) => (
                    <button key={a.id}
                      onClick={() => router.push("/assignments")}
                      className="card card-hover w-full text-left flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{a.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(a.course_id)} · {a.days_left}d left
                        </div>
                      </div>
                      <RiskBadge level={a.risk_level} />
                    </button>
                  ))}
                </div>
              ) : (
                <Empty>No high-risk items detected.</Empty>
              )}
            </Section>
          </div>

          <Section title="Concepts to review">
            {data.review_due?.length ? (
              <div className="flex flex-wrap gap-2">
                {data.review_due.map((r: any) => (
                  <Badge key={r.concept_id} tone={gradeTone((r.retention ?? 0) * 100)}>
                    {r.name ?? "concept"} · {(r.retention ?? 0).toFixed?.(2) ?? r.retention}
                  </Badge>
                ))}
              </div>
            ) : (
              <Empty>Nothing due for review right now.</Empty>
            )}
          </Section>
        </>
      )}
    </AppShell>
  );
}
