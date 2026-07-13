"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Stat, Section, Empty, Loading, Badge } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

export default function AnalyticsPage() {
  const [snap, setSnap] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiGet("/analytics/snapshot").then(setSnap).catch(() => setSnap({ error: true }));
  }, []);

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
      {!snap && <Loading />}
      {snap && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <Stat label="GPA (weighted)" value={snap.predicted_gpa_weighted ?? "—"} tone="good" />
            <Stat label="GPA (unweighted)" value={snap.predicted_gpa_unweighted ?? "—"} />
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
                  <div key={t.course_id} className="card flex items-center justify-between">
                    <span className="text-sm">{t.first}% → {t.latest}% ({t.samples} grades)</span>
                    <Badge tone={t.direction === "up" ? "good" : t.direction === "down" ? "bad" : "default"}>
                      {t.direction} {t.delta > 0 ? `+${t.delta}` : t.delta}
                    </Badge>
                  </div>
                ))}
              </div>
            ) : <Empty>Not enough graded work yet to show trends.</Empty>}
          </Section>

          <Section title="At-risk assignments">
            {snap.at_risk?.length ? (
              <div className="space-y-2">
                {snap.at_risk.map((a: any) => (
                  <div key={a.id} className="card flex items-center justify-between">
                    <span className="text-sm">{a.title} · {a.days_left}d left</span>
                    <Badge tone="bad">risk {a.risk_score}</Badge>
                  </div>
                ))}
              </div>
            ) : <Empty>No high-risk items right now.</Empty>}
          </Section>
        </>
      )}
    </AppShell>
  );
}
