"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Stat, Section, Empty, Loading, gradeTone, Badge } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

export default function DashboardPage() {
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
            <Stat
              label="Predicted GPA"
              value={data.predicted_gpa ?? "—"}
              tone="good"
              hint="weighted"
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
            <Section title="Today's plan">
              <div className="card">
                <p className="text-sm">{data.daily_plan.summary}</p>
                <div className="mt-3 space-y-1.5">
                  {(data.daily_plan.blocks ?? []).map((b: any, i: number) => (
                    <div key={i} className="flex items-center gap-3 text-sm">
                      <span className="text-atlas-accent2 font-mono text-xs w-24">
                        {b.start}–{b.end}
                      </span>
                      <span>{b.task}</span>
                    </div>
                  ))}
                </div>
                {data.daily_plan.motivational_note && (
                  <p className="text-xs text-atlas-muted mt-3 italic">
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
                    <div key={a.id} className="card card-hover flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium">{a.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(a.course_id)} · {a.category}
                        </div>
                      </div>
                      <Badge tone="accent">
                        {a.due_date ? new Date(a.due_date).toLocaleDateString() : "—"}
                      </Badge>
                    </div>
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
                    <div key={a.id} className="card flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium">{a.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(a.course_id)} · {a.days_left}d left
                        </div>
                      </div>
                      <Badge tone="bad">risk {a.risk_score}</Badge>
                    </div>
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
