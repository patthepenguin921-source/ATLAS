"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Stat, RevealStat, Section, Empty, SkeletonStats, Badge, RiskBadge } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

// Matches the labels used on the assignment detail page's "What did you get
// wrong?" logger (frontend/src/app/assignments/page.tsx) so the same
// mistake_type reads the same way everywhere.
const MISTAKE_TYPE_LABEL: Record<string, string> = {
  conceptual: "Conceptual",
  careless: "Careless",
  procedural: "Procedural",
  knowledge_gap: "Knowledge gap",
  unspecified: "Uncategorized",
};

export default function AnalyticsPage() {
  const router = useRouter();
  const [snap, setSnap] = useState<any>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiGet("/analytics/snapshot").then(setSnap).catch(() => setSnap({ error: true }));
    apiGet("/courses").then(setCourses).catch(() => setCourses([]));
  }, []);

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "Course";

  async function runAnalyst() {
    setBusy(true);
    try {
      const r = await apiPost("/agents/analyst/analyze", {});
      setAnalysis(r.analysis);
    } finally {
      setBusy(false);
    }
  }

  const eff = snap?.study_efficiency;

  return (
    <AppShell
      title="Analytics"
      subtitle="Patterns you can't see on your own"
      actions={<button className="btn-primary" onClick={runAnalyst} disabled={busy}>
        {busy ? "Analyzing…" : "Ask the Analyst"}
      </button>}
    >
      {!snap && <SkeletonStats count={4} />}
      {snap && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <RevealStat label="GPA (unweighted)" value={snap.predicted_gpa_unweighted ?? "—"} tone="good" />
            <RevealStat label="GPA (weighted)" value={snap.predicted_gpa_weighted ?? "—"} />
            <Stat label="Study (30d)" value={`${eff?.total_minutes ?? 0}m`}
              hint={`${eff?.sessions ?? 0} sessions`} />
            <Stat label="Avg focus" value={eff?.avg_focus ?? "—"}
              hint={eff?.most_productive_hour != null ? `peak ${eff.most_productive_hour}:00` : undefined} />
          </div>

          {analysis && (
            <Section title="Analyst report">
              <div className="card space-y-3">
                <div className="text-sm font-medium text-atlas-accent2">{analysis.headline}</div>
                {["trends", "risks", "strengths", "recommendations"].map((k) =>
                  analysis[k]?.length ? (
                    <div key={k}>
                      <div className="text-xs uppercase text-atlas-muted mb-1">{k}</div>
                      <ul className="text-sm list-disc list-inside space-y-0.5">
                        {analysis[k].map((x: string, i: number) => <li key={i}>{x}</li>)}
                      </ul>
                    </div>
                  ) : null
                )}
              </div>
            </Section>
          )}

          <Section title="Grade trends">
            {snap.grade_trends?.length ? (
              <div className="space-y-2">
                {snap.grade_trends.map((t: any) => (
                  <button
                    key={t.course_id}
                    onClick={() => router.push(`/courses/${t.course_id}`)}
                    className="card card-hover w-full text-left flex items-center justify-between"
                  >
                    <span className="text-sm">
                      <span className="font-medium">{courseName(t.course_id)}</span>
                      <span className="text-atlas-muted"> · {t.first}% → {t.latest}% ({t.samples} grades)</span>
                    </span>
                    <Badge tone={t.direction === "up" ? "good" : t.direction === "down" ? "bad" : "default"}>
                      {t.direction} {t.delta > 0 ? `+${t.delta}` : t.delta}
                    </Badge>
                  </button>
                ))}
              </div>
            ) : <Empty>Not enough graded work yet to show trends.</Empty>}
          </Section>

          <Section title="At-risk assignments">
            {snap.at_risk?.length ? (
              <div className="space-y-2">
                {snap.at_risk.map((a: any) => (
                  <button
                    key={a.id}
                    onClick={() => router.push(`/assignments?id=${a.id}`)}
                    className="card card-hover w-full text-left flex items-center justify-between gap-3"
                    title="View in Assignments"
                  >
                    <span className="text-sm min-w-0">
                      <span className="font-medium truncate">{a.title}</span>
                      <span className="text-atlas-muted"> · {courseName(a.course_id)} · {a.days_left}d left</span>
                    </span>
                    <RiskBadge level={a.risk_level} />
                  </button>
                ))}
              </div>
            ) : <Empty>No high-risk items right now.</Empty>}
          </Section>

          <Section title="Recurring mistakes">
            {snap.mistake_patterns?.length ? (
              <div className="space-y-2">
                {snap.mistake_patterns.map((p: any, i: number) => (
                  <button
                    key={i}
                    onClick={() => p.course_id && router.push(`/courses/${p.course_id}`)}
                    className="card card-hover w-full text-left flex items-center justify-between gap-3"
                  >
                    <span className="text-sm min-w-0">
                      <span className="font-medium">{courseName(p.course_id)}</span>
                      <span className="text-atlas-muted"> · {MISTAKE_TYPE_LABEL[p.mistake_type] ?? p.mistake_type} · {p.count}x</span>
                    </span>
                    {p.unresolved > 0 && <Badge tone="warn">{p.unresolved} unresolved</Badge>}
                  </button>
                ))}
              </div>
            ) : (
              <Empty>
                Nothing logged yet — log what you got wrong (and why) from an assignment's detail view to see patterns here.
              </Empty>
            )}
          </Section>
        </>
      )}
    </AppShell>
  );
}
