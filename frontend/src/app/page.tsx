"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Stat, RevealStat, Section, Empty, Loading, gradeTone, Badge, RiskBadge } from "@/components/ui";
import { apiGet, apiPost, apiUpload } from "@/lib/api";
import { formatCalendarDate } from "@/lib/date";

const UPLOAD_ACCEPT = ".pdf,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.heic,.heif";

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

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<{ assignments: any[]; documents: any[] } | null>(null);

  const [uploadCourseId, setUploadCourseId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const uploadFileRef = useRef<HTMLInputElement>(null);

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

  // Debounced quick search across assignments + documents (trigram text
  // search — fast enough to fire on typing, unlike the semantic endpoint).
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        setResults(await apiGet(`/search/text?q=${encodeURIComponent(q)}&limit=6`));
      } catch {
        setResults(null);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  async function uploadDocument(e: React.FormEvent) {
    e.preventDefault();
    const file = uploadFileRef.current?.files?.[0];
    if (!file) return;
    if (!uploadCourseId) {
      setUploadStatus({ ok: false, text: "Pick a course first." });
      return;
    }
    setUploading(true);
    setUploadPct(0);
    setUploadStatus(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("course_id", uploadCourseId);
      await apiUpload("/documents/upload", form, setUploadPct);
      setUploadStatus({ ok: true, text: "Uploaded — processing in the background." });
      if (uploadFileRef.current) uploadFileRef.current.value = "";
    } catch (err: any) {
      setUploadStatus({ ok: false, text: err.message });
    } finally {
      setUploading(false);
      setUploadPct(null);
    }
  }

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
  const activeCourses = (data?.courses ?? []).filter((c: any) => c.is_active !== false);

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

          <Section title="Documents">
            <div className="grid md:grid-cols-2 gap-4">
              <div className="card">
                <label className="label">Search assignments &amp; documents</label>
                <input
                  className="input w-full"
                  placeholder="e.g. photosynthesis, unit 3 review…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {searching && <div className="text-xs text-atlas-muted mt-2">Searching…</div>}
                {results && !searching && (
                  <div className="mt-3 space-y-1 max-h-56 overflow-auto">
                    {!results.documents?.length && !results.assignments?.length && (
                      <div className="text-xs text-atlas-muted">No matches.</div>
                    )}
                    {results.documents?.map((d: any) => (
                      <button
                        key={d.id}
                        className="w-full text-left text-sm px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
                        onClick={() => router.push(`/documents/${d.id}`)}
                      >
                        <div className="truncate">
                          📄 {d.title} <span className="text-xs text-atlas-muted">· {courseName(d.course_id)}</span>
                        </div>
                        {d.snippet && (
                          <div className="text-xs text-atlas-muted truncate pl-5" title={d.snippet}>
                            {d.snippet}
                          </div>
                        )}
                      </button>
                    ))}
                    {results.assignments?.map((a: any) => (
                      <button
                        key={a.id}
                        className="w-full text-left text-sm px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 truncate"
                        onClick={() => router.push("/assignments")}
                      >
                        📝 {a.title} <span className="text-xs text-atlas-muted">· {courseName(a.course_id)}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <form className="card" onSubmit={uploadDocument}>
                <label className="label">Quick upload</label>
                <div className="flex flex-wrap items-end gap-2">
                  <select
                    className="input !w-40"
                    value={uploadCourseId}
                    onChange={(e) => setUploadCourseId(e.target.value)}
                    required
                  >
                    <option value="">Select a class…</option>
                    {activeCourses.map((c: any) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                  <input ref={uploadFileRef} type="file" className="text-sm" required accept={UPLOAD_ACCEPT} />
                  <button className="btn-primary !py-1.5" disabled={uploading}>
                    {uploading
                      ? uploadPct != null && uploadPct < 100
                        ? `Uploading… ${uploadPct}%`
                        : "Processing…"
                      : "Upload"}
                  </button>
                </div>
                {uploadStatus && (
                  <div className={`text-xs mt-2 ${uploadStatus.ok ? "text-atlas-good" : "text-atlas-bad"}`}>
                    {uploadStatus.text}
                  </div>
                )}
                <button
                  type="button"
                  className="text-xs text-atlas-muted hover:text-atlas-text mt-2"
                  onClick={() => router.push("/documents")}
                >
                  Manage all documents →
                </button>
              </form>
            </div>
          </Section>

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
                        {a.due_date ? formatCalendarDate(a.due_date) : "—"}
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
