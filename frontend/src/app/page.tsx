"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { DailyPlanCard } from "@/components/DailyPlanCard";
import { Stat, RevealStat, Section, Empty, SkeletonStats, SkeletonList, gradeTone, Badge, RiskBadge } from "@/components/ui";
import { apiGet, apiPost, apiUpload } from "@/lib/api";
import { formatCalendarDate } from "@/lib/date";

const UPLOAD_ACCEPT = ".pdf,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.heic,.heif";

// Tag filter options for the quick search below — "doc:"/"cat:" prefixes
// disambiguate document doc_types from assignment categories that happen to
// share a name (e.g. "essay", "other") since they're two different backend
// fields (`doc_type` vs `category`) sent as two different query params.
const DOC_TYPE_TAGS: [string, string][] = [
  ["pdf", "PDF"], ["powerpoint", "Slides"], ["notes", "Notes"], ["announcement", "Announcement"],
  ["study_guide", "Study guide"], ["essay", "Essay"], ["practice_problems", "Practice problems"],
  ["rubric", "Rubric"], ["personal_note", "Personal note"], ["email", "Email"], ["image", "Image"],
  ["glance", "At a glance"], ["other", "Other"],
];
const CATEGORY_TAGS: [string, string][] = [
  ["homework", "Homework"], ["classwork", "Classwork"], ["quiz", "Quiz"], ["test", "Test"],
  ["exam", "Exam"], ["project", "Project"], ["essay", "Essay"], ["lab", "Lab"],
  ["discussion", "Discussion"], ["presentation", "Presentation"], ["reading", "Reading"],
  ["participation", "Participation"], ["other", "Other"],
];

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  const [query, setQuery] = useState("");
  const [tagFilter, setTagFilter] = useState(""); // "" | "doc:<doc_type>" | "cat:<category>"
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
  // Also fires with an empty query when a tag is picked, so clicking e.g.
  // "At a glance" alone browses every glance doc without typing anything.
  useEffect(() => {
    const q = query.trim();
    if (!q && !tagFilter) {
      setResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q, limit: "6" });
        if (tagFilter.startsWith("doc:")) params.set("doc_type", tagFilter.slice(4));
        if (tagFilter.startsWith("cat:")) params.set("category", tagFilter.slice(4));
        setResults(await apiGet(`/search/text?${params.toString()}`));
      } catch {
        setResults(null);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, tagFilter]);

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
      // No available_minutes -- the backend reads today's real availability
      // from the student's own weekly schedule (see /study-availability,
      // configured on the Knowledge page) instead of a flat guess.
      await apiPost("/agents/planner/daily-plan", {});
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
      {!data && !error && (
        <>
          <SkeletonStats count={4} />
          <div className="mt-6">
            <SkeletonList rows={3} />
          </div>
        </>
      )}

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
                <div className="flex gap-2">
                  <input
                    className="input w-full"
                    placeholder="e.g. photosynthesis, unit 3 review…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <select
                    className="input !w-36 shrink-0"
                    value={tagFilter}
                    onChange={(e) => setTagFilter(e.target.value)}
                    title="Filter by tag"
                  >
                    <option value="">All tags</option>
                    <optgroup label="Documents">
                      {DOC_TYPE_TAGS.map(([value, label]) => (
                        <option key={`doc:${value}`} value={`doc:${value}`}>{label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Assignments">
                      {CATEGORY_TAGS.map(([value, label]) => (
                        <option key={`cat:${value}`} value={`cat:${value}`}>{label}</option>
                      ))}
                    </optgroup>
                  </select>
                </div>
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
              <DailyPlanCard plan={data.daily_plan} />
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
