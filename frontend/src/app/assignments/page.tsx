"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, Modal, RiskBadge, SkeletonList } from "@/components/ui";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { formatCalendarDate } from "@/lib/date";
import { buildFolderOptions } from "@/lib/folderOptions";

// Must match Postgres's `assignment_category` enum (0001_extensions_and_types.sql)
// exactly -- this list previously omitted 6 of the 13 real values (classwork,
// exam, discussion, presentation, reading, participation), so e.g. "exam" was
// never selectable here even though the risk calculation treats it specially.
const CATEGORIES = [
  "homework", "classwork", "quiz", "test", "exam", "project", "essay",
  "lab", "discussion", "presentation", "reading", "participation", "other",
];
const STATUSES = ["not_started", "in_progress", "submitted", "graded", "missing"];

// Matches backend/app/services/grading.py's ASSIGNMENT_WEIGHTS -- stored
// directly in the assignment's existing `weight` column and folded into the
// course's grade rollup (see supabase/migrations/0023_assignment_weight_grade_rollup.sql)
// and the at-risk calculation.
const WEIGHT_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Not set" },
  { value: "0.3", label: "Minor (30%)" },
  { value: "0.7", label: "Major (70%)" },
];

const statusTone = (s: string) =>
  s === "graded" || s === "submitted" ? "good" : s === "missing" ? "bad" : "default";

// 1 (easiest) .. 5 (hardest) -- feeds directly into the at-risk score (see
// backend/app/services/analytics.py's at_risk_assignments), same as weight.
const DIFFICULTY_OPTIONS = [1, 2, 3, 4, 5];

// Manually pins the risk badge instead of only ever nudging it indirectly
// via weight/difficulty/points -- see assignments.risk_override
// (migration 0029) and analytics.py's _risk_level, which always lets this
// win over the computed score when set.
const RISK_OVERRIDE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Auto (computed)" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extreme", label: "Extreme" },
];

// A "completed" assignment is one with a real result -- graded or turned
// in -- not just past its due date (still-open overdue/missing work stays
// in Upcoming, where it needs attention, not buried in a history tab).
const isCompleted = (a: any) => a.status === "graded" || a.status === "submitted";

// Stable identity for a duplicate pair regardless of which order the
// backend's detector happens to compare the two assignments in -- used to
// key both the "which one to keep" choice and the busy-state map below.
const pairKey = (a: string, b: string) => (a < b ? `${a}:${b}` : `${b}:${a}`);

export default function AssignmentsPage() {
  const [items, setItems] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [risk, setRisk] = useState<Record<string, { risk_level: string; risk_score: number }>>({});
  const [grades, setGrades] = useState<Record<string, any>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"upcoming" | "completed">("upcoming");
  const [courseFilter, setCourseFilter] = useState("");
  // Possible-duplicate review (see app.services.assignment_dedupe) -- fetched
  // separately from `load()` below so a hiccup in this still-new endpoint
  // can never block the assignments list itself from loading.
  const [duplicates, setDuplicates] = useState<any[]>([]);
  const [keepChoice, setKeepChoice] = useState<Record<string, string>>({});
  const [dupBusy, setDupBusy] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<any | null>(null);
  const [editing, setEditing] = useState(false);
  const [formFolders, setFormFolders] = useState<any[]>([]);
  const [detailFolders, setDetailFolders] = useState<any[]>([]);
  const [gradeEditing, setGradeEditing] = useState(false);
  const [gradeForm, setGradeForm] = useState({ score: "", points_possible: "", letter: "" });
  const [savingGrade, setSavingGrade] = useState(false);
  const [detail, setDetail] = useState<{
    description: string; notes: string; weight: string; difficulty: string; points_possible: string;
    risk_override: string; folder_id: string;
  }>({ description: "", notes: "", weight: "", difficulty: "", points_possible: "", risk_override: "", folder_id: "" });
  const [form, setForm] = useState<any>({
    title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30,
    description: "", notes: "", weight: "", folder_id: "",
  });

  async function load() {
    try {
      const [a, c, r, g] = await Promise.all([
        apiGet("/assignments"), apiGet("/courses"), apiGet("/analytics/at-risk?limit=100"),
        apiGet("/grades?limit=500"),
      ]);
      setItems(a);
      setCourses(c);
      setRisk(Object.fromEntries((r ?? []).map((x: any) => [x.id, x])));
      setGrades(Object.fromEntries((g ?? []).filter((x: any) => x.assignment_id).map((x: any) => [x.assignment_id, x])));
      setLoadError(null);
    } catch (e: any) {
      // Without this, a failed request left `items` at its initial `null`
      // forever -- the skeleton below just spins with no explanation.
      setLoadError(e.message);
    }
  }
  async function loadDuplicates() {
    try {
      setDuplicates((await apiGet("/assignments/possible-duplicates")) ?? []);
    } catch {
      // Non-critical: the review section just stays empty rather than
      // taking the whole page down with it.
      setDuplicates([]);
    }
  }
  useEffect(() => {
    load();
    loadDuplicates();
  }, []);

  // Units/subunits are per-class (see `folders`, migration 0024) -- refetch
  // whenever the add form's course changes, same pattern as the Study
  // page's Practice/Flashcards tabs.
  useEffect(() => {
    if (!form.course_id) {
      setFormFolders([]);
      return;
    }
    apiGet(`/folders?course_id=${form.course_id}`).then((f) => setFormFolders(f ?? []));
  }, [form.course_id]);

  useEffect(() => {
    if (!selected?.course_id) {
      setDetailFolders([]);
      return;
    }
    apiGet(`/folders?course_id=${selected.course_id}`).then((f) => setDetailFolders(f ?? []));
  }, [selected?.course_id]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const body = { ...form };
    if (body.due_date) body.due_date = new Date(body.due_date).toISOString();
    if (!body.course_id) delete body.course_id;
    if (body.weight) body.weight = Number(body.weight);
    else delete body.weight;
    if (!body.folder_id) delete body.folder_id;
    await apiPost("/assignments", body);
    setForm({ title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30,
      description: "", notes: "", weight: "", folder_id: "" });
    setOpen(false);
    load();
  }

  function openDetail(a: any) {
    setSelected(a);
    setEditing(false);
    setGradeEditing(false);
    setDetail({
      description: a.description ?? "", notes: a.notes ?? "",
      weight: a.weight != null ? String(a.weight) : "",
      difficulty: a.difficulty != null ? String(a.difficulty) : "",
      points_possible: a.points_possible != null ? String(a.points_possible) : "",
      risk_override: a.risk_override ?? "",
      folder_id: a.folder_id ?? "",
    });
    const g = grades[a.id];
    setGradeForm({
      score: g?.score != null ? String(g.score) : "",
      points_possible: g?.points_possible != null ? String(g.points_possible)
        : a.points_possible != null ? String(a.points_possible) : "",
      letter: g?.letter ?? "",
    });
  }

  async function saveDetail() {
    if (!selected) return;
    const patch = {
      description: detail.description,
      notes: detail.notes,
      weight: detail.weight ? Number(detail.weight) : null,
      difficulty: detail.difficulty ? Number(detail.difficulty) : null,
      points_possible: detail.points_possible ? Number(detail.points_possible) : null,
      risk_override: detail.risk_override || null,
      folder_id: detail.folder_id || null,
    };
    await apiPatch(`/assignments/${selected.id}`, patch);
    setSelected((s: any) => (s ? { ...s, ...patch } : s));
    setEditing(false);
    load();
  }

  // Manual grade entry -- for when PowerSchool/Schoology never synced a
  // grade for something (or it was never connected at all). Updates the
  // existing `grades` row if one's already on file, otherwise creates one;
  // either way the course's rolling grade recomputes automatically (see
  // `recompute_course_grade`'s trigger on the `grades` table).
  async function saveGrade() {
    if (!selected) return;
    setSavingGrade(true);
    try {
      const payload: any = {
        course_id: selected.course_id,
        assignment_id: selected.id,
        score: gradeForm.score ? Number(gradeForm.score) : null,
        points_possible: gradeForm.points_possible ? Number(gradeForm.points_possible) : null,
        letter: gradeForm.letter || null,
        graded_at: new Date().toISOString(),
      };
      const existing = grades[selected.id];
      const saved = existing
        ? await apiPatch(`/grades/${existing.id}`, payload)
        : await apiPost("/grades", payload);
      setGrades((prev) => ({ ...prev, [selected.id]: saved }));
      if (selected.status !== "graded") {
        await apiPatch(`/assignments/${selected.id}`, { status: "graded" });
        setSelected((s: any) => (s ? { ...s, status: "graded" } : s));
      }
      setGradeEditing(false);
      load();
    } finally {
      setSavingGrade(false);
    }
  }

  async function setStatus(id: string, status: string) {
    await apiPatch(`/assignments/${id}`, {
      status,
      ...(status === "submitted" ? { submitted_at: new Date().toISOString() } : {}),
    });
    setSelected((s: any) => (s && s.id === id ? { ...s, status } : s));
    load();
  }

  async function remove(id: string) {
    await apiDelete(`/assignments/${id}`);
    setSelected(null);
    load();
  }

  async function mergeDuplicate(candidate: any) {
    const [a, b] = candidate.assignments;
    const key = pairKey(a.id, b.id);
    const keepId = keepChoice[key] ?? candidate.suggested_keep_id;
    const discardId = keepId === a.id ? b.id : a.id;
    setDupBusy((s) => ({ ...s, [key]: true }));
    try {
      await apiPost("/assignments/merge", { keep_id: keepId, discard_id: discardId });
      setDuplicates((ds) => ds.filter((d) => pairKey(d.assignments[0].id, d.assignments[1].id) !== key));
      load();
    } finally {
      setDupBusy((s) => ({ ...s, [key]: false }));
    }
  }

  async function dismissDuplicate(candidate: any) {
    const [a, b] = candidate.assignments;
    const key = pairKey(a.id, b.id);
    setDupBusy((s) => ({ ...s, [key]: true }));
    try {
      await apiPost("/assignments/dismiss-duplicate", { assignment_id_a: a.id, assignment_id_b: b.id });
      setDuplicates((ds) => ds.filter((d) => pairKey(d.assignments[0].id, d.assignments[1].id) !== key));
    } finally {
      setDupBusy((s) => ({ ...s, [key]: false }));
    }
  }

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "—";
  const weightLabel = (w: number | null | undefined) =>
    w === 0.3 ? "Minor (30%)" : w === 0.7 ? "Major (70%)" : w != null ? `${Math.round(w * 100)}%` : null;
  const formFolderOptions = buildFolderOptions(formFolders);
  const detailFolderOptions = buildFolderOptions(detailFolders);
  const inCourseFilter = (a: any) => !courseFilter || a.course_id === courseFilter;
  const visibleItems = (items ?? [])
    .filter((a) => (tab === "completed" ? isCompleted(a) : !isCompleted(a)))
    .filter(inCourseFilter);

  return (
    <AppShell
      title="Assignments"
      subtitle="Everything on your plate"
      actions={<button className="btn-primary" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "Add"}</button>}
    >
      {open && (
        <form onSubmit={add} className="card mb-6 grid md:grid-cols-5 gap-3 items-end">
          <div className="md:col-span-2">
            <label className="label">Title</label>
            <input className="input" required value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <label className="label">Course</label>
            <select className="input" value={form.course_id}
              onChange={(e) => setForm({ ...form, course_id: e.target.value })}>
              <option value="">—</option>
              {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Category</label>
            <select className="input" value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Due</label>
            <input className="input" type="datetime-local" value={form.due_date}
              onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
          </div>
          <div>
            <label className="label">Weight</label>
            <select className="input" value={form.weight}
              onChange={(e) => setForm({ ...form, weight: e.target.value })}>
              {WEIGHT_OPTIONS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Unit</label>
            <select className="input" value={form.folder_id}
              disabled={!form.course_id || !formFolderOptions.length}
              onChange={(e) => setForm({ ...form, folder_id: e.target.value })}>
              <option value="">{form.course_id ? "Not categorized" : "Pick a course first"}</option>
              {formFolderOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </div>
          <div className="md:col-span-5">
            <label className="label">Details & instructions</label>
            <textarea className="input min-h-[70px]" value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Instructions, rubric, what's expected…" />
          </div>
          <div className="md:col-span-5">
            <label className="label">Notes</label>
            <textarea className="input min-h-[50px]" value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="Your own notes for this assignment…" />
          </div>
          <button className="btn-primary md:col-span-5">Save assignment</button>
        </form>
      )}

      {duplicates.length > 0 && (
        <div className="card border-atlas-warn/40 mb-6 space-y-3">
          <div>
            <div className="text-sm font-medium text-atlas-warn">
              Possible duplicate assignments ({duplicates.length})
            </div>
            <div className="text-xs text-atlas-muted mt-0.5">
              These look like the same assignment entered or synced twice. Pick which one to keep, or tell us they're actually different.
            </div>
          </div>
          {duplicates.map((d) => {
            const [a, b] = d.assignments;
            const key = pairKey(a.id, b.id);
            const keep = keepChoice[key] ?? d.suggested_keep_id;
            const busy = !!dupBusy[key];
            const keepTitle = (keep === a.id ? a : b).title;
            return (
              <div key={key} className="border border-atlas-border rounded-lg p-3 space-y-2">
                <div className="grid sm:grid-cols-2 gap-2">
                  {[a, b].map((x) => (
                    <label
                      key={x.id}
                      className={`flex items-start gap-2 rounded-md border p-2 cursor-pointer text-sm ${
                        keep === x.id ? "border-atlas-accent" : "border-atlas-border"
                      }`}
                    >
                      <input
                        type="radio"
                        className="mt-1"
                        checked={keep === x.id}
                        onChange={() => setKeepChoice((k) => ({ ...k, [key]: x.id }))}
                      />
                      <div className="min-w-0">
                        <div className="font-medium truncate">{x.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(x.course_id)} · {x.category}
                          {x.due_date && ` · due ${formatCalendarDate(x.due_date)}`}
                          {x.external_source && ` · from ${x.external_source}`}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn-primary text-xs py-1.5"
                    disabled={busy}
                    onClick={() => mergeDuplicate(d)}
                  >
                    {busy ? "Merging…" : `Keep "${keepTitle}", merge the other in`}
                  </button>
                  <button className="btn-ghost text-xs py-1.5" disabled={busy} onClick={() => dismissDuplicate(d)}>
                    Not duplicates
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!items && !loadError && <SkeletonList rows={4} />}
      {loadError && !items && (
        <div className="card border-atlas-bad/40 text-sm mb-6">
          <div className="font-medium text-atlas-bad">Couldn't load your assignments</div>
          <div className="text-atlas-muted mt-1">{loadError}</div>
          <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
        </div>
      )}
      {items && !items.length && <Empty>No assignments yet.</Empty>}

      {items && items.length > 0 && (
        <div className="flex items-center justify-between gap-3 flex-wrap mb-4 border-b border-atlas-border">
          <div className="flex gap-1.5">
            {([
              ["upcoming", "Upcoming"],
              ["completed", "Past & graded"],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`px-3 py-2 -mb-px text-sm font-medium border-b-2 transition-colors ${
                  tab === id
                    ? "border-atlas-accent text-atlas-text"
                    : "border-transparent text-atlas-muted hover:text-atlas-text hover:border-atlas-border"
                }`}
              >
                {label} (
                {(items ?? [])
                  .filter((a) => (id === "completed" ? isCompleted(a) : !isCompleted(a)))
                  .filter(inCourseFilter).length}
                )
              </button>
            ))}
          </div>
          <select
            className="input !w-auto text-xs py-1.5 mb-1.5"
            value={courseFilter}
            onChange={(e) => setCourseFilter(e.target.value)}
          >
            <option value="">All classes</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      )}
      {items && items.length > 0 && !visibleItems.length && (
        <Empty>{tab === "completed" ? "Nothing completed or graded yet." : "Nothing upcoming — everything's done."}</Empty>
      )}
      <div className="space-y-2">
        {visibleItems.map((a) => (
          <div
            key={a.id}
            className="card card-hover flex items-center justify-between gap-4 cursor-pointer"
            onClick={() => openDetail(a)}
          >
            <div className="min-w-0">
              <div className="font-medium truncate flex items-center gap-2">
                {a.title}
                {a.description && (
                  <span className="text-[10px] text-atlas-muted border border-atlas-border rounded px-1 py-0.5">
                    details
                  </span>
                )}
              </div>
              <div className="text-xs text-atlas-muted">
                {courseName(a.course_id)} · {a.category}
                {weightLabel(a.weight) && ` · ${weightLabel(a.weight)}`}
                {a.due_date && ` · due ${formatCalendarDate(a.due_date)}`}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0" onClick={(e) => e.stopPropagation()}>
              {isCompleted(a) && (
                grades[a.id]?.percentage != null ? (
                  <Badge tone="good">{grades[a.id].percentage}%{grades[a.id].letter ? ` (${grades[a.id].letter})` : ""}</Badge>
                ) : (
                  <Badge tone="warn">no grade yet</Badge>
                )
              )}
              {risk[a.id] && <RiskBadge level={risk[a.id].risk_level} />}
              <Badge tone={statusTone(a.status) as any}>{a.status.replace("_", " ")}</Badge>
              <select
                className="input !w-auto text-xs py-1"
                value={a.status}
                onChange={(e) => setStatus(a.id, e.target.value)}
              >
                {STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
              </select>
            </div>
          </div>
        ))}
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.title}
        draggable
        resizable
        footer={
          selected && (
            <>
              <button
                className="btn-ghost text-atlas-bad hover:!border-atlas-bad/50 mr-auto"
                onClick={() => remove(selected.id)}
              >
                Delete
              </button>
              {editing ? (
                <>
                  <button className="btn-ghost" onClick={() => setEditing(false)}>Cancel</button>
                  <button className="btn-primary" onClick={saveDetail}>Save</button>
                </>
              ) : (
                <>
                  <button className="btn-ghost" onClick={() => setEditing(true)}>Edit</button>
                  <button
                    className="btn-primary"
                    onClick={() => setStatus(selected.id, "graded")}
                    disabled={selected.status === "graded"}
                  >
                    {selected.status === "graded" ? "Completed" : "Mark complete"}
                  </button>
                </>
              )}
            </>
          )
        }
      >
        {selected && (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge tone={statusTone(selected.status) as any}>{selected.status.replace("_", " ")}</Badge>
              <Badge>{selected.category}</Badge>
              <Badge tone="accent">{courseName(selected.course_id)}</Badge>
              {risk[selected.id] && <RiskBadge level={risk[selected.id].risk_level} />}
            </div>
            {selected.due_date && (
              <div className="text-atlas-muted">
                Due {formatCalendarDate(selected.due_date)}
              </div>
            )}

            {editing ? (
              <>
                <div>
                  <div className="label">Details & instructions</div>
                  <textarea className="input min-h-[90px]" value={detail.description}
                    onChange={(e) => setDetail({ ...detail, description: e.target.value })}
                    placeholder="Instructions, rubric, what's expected…" />
                </div>
                <div>
                  <div className="label">Notes</div>
                  <textarea className="input min-h-[70px]" value={detail.notes}
                    onChange={(e) => setDetail({ ...detail, notes: e.target.value })}
                    placeholder="Your own notes…" />
                </div>
                <div>
                  <div className="label">Risk factors (drive the risk badge above)</div>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label className="text-xs text-atlas-muted">Weight</label>
                      <select className="input text-sm" value={detail.weight}
                        onChange={(e) => setDetail({ ...detail, weight: e.target.value })}>
                        {WEIGHT_OPTIONS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-atlas-muted">Difficulty</label>
                      <select className="input text-sm" value={detail.difficulty}
                        onChange={(e) => setDetail({ ...detail, difficulty: e.target.value })}>
                        <option value="">Not set</option>
                        {DIFFICULTY_OPTIONS.map((d) => <option key={d} value={d}>{d}/5</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-atlas-muted">Points</label>
                      <input type="number" min={0} className="input text-sm" value={detail.points_possible}
                        onChange={(e) => setDetail({ ...detail, points_possible: e.target.value })} />
                    </div>
                  </div>
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Risk level override</label>
                  <select className="input text-sm" value={detail.risk_override}
                    onChange={(e) => setDetail({ ...detail, risk_override: e.target.value })}>
                    {RISK_OVERRIDE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                  <p className="text-[11px] text-atlas-muted mt-1">
                    Pin the risk badge directly instead of relying on weight/difficulty/points.
                  </p>
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Unit</label>
                  <select className="input text-sm" value={detail.folder_id}
                    disabled={!detailFolderOptions.length}
                    onChange={(e) => setDetail({ ...detail, folder_id: e.target.value })}>
                    <option value="">Not categorized</option>
                    {detailFolderOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                </div>
              </>
            ) : (
              <>
                <div>
                  <div className="text-xs uppercase text-atlas-muted mb-1">Details & instructions</div>
                  {selected.description ? (
                    <p className="whitespace-pre-wrap">{selected.description}</p>
                  ) : (
                    <p className="text-atlas-muted italic">No details yet — tap Edit to add instructions.</p>
                  )}
                </div>
                <div>
                  <div className="text-xs uppercase text-atlas-muted mb-1">Notes</div>
                  {selected.notes ? (
                    <p className="whitespace-pre-wrap">{selected.notes}</p>
                  ) : (
                    <p className="text-atlas-muted italic">No notes yet.</p>
                  )}
                </div>
              </>
            )}

            <div className="border-t border-atlas-border pt-3">
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-xs uppercase text-atlas-muted">Grade</div>
                {!gradeEditing && (
                  <button className="text-xs text-atlas-accent hover:underline" onClick={() => setGradeEditing(true)}>
                    {grades[selected.id] ? "Edit grade" : "Enter grade"}
                  </button>
                )}
              </div>
              {gradeEditing ? (
                <div className="grid grid-cols-3 gap-2 items-end">
                  <div>
                    <label className="text-xs text-atlas-muted">Score</label>
                    <input type="number" step="0.01" className="input text-sm" value={gradeForm.score}
                      onChange={(e) => setGradeForm({ ...gradeForm, score: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-xs text-atlas-muted">Out of</label>
                    <input type="number" step="0.01" className="input text-sm" value={gradeForm.points_possible}
                      onChange={(e) => setGradeForm({ ...gradeForm, points_possible: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-xs text-atlas-muted">Letter (optional)</label>
                    <input className="input text-sm" value={gradeForm.letter}
                      onChange={(e) => setGradeForm({ ...gradeForm, letter: e.target.value })} />
                  </div>
                  <div className="col-span-3 flex gap-2 mt-1">
                    <button className="btn-primary text-xs py-1.5" onClick={saveGrade} disabled={savingGrade}>
                      {savingGrade ? "Saving…" : "Save grade"}
                    </button>
                    <button className="btn-ghost text-xs py-1.5" onClick={() => setGradeEditing(false)}>Cancel</button>
                  </div>
                </div>
              ) : grades[selected.id] ? (
                <div className="text-sm">
                  {grades[selected.id].percentage != null ? `${grades[selected.id].percentage}%` : "—"}
                  {grades[selected.id].letter ? ` (${grades[selected.id].letter})` : ""}
                  {grades[selected.id].score != null && grades[selected.id].points_possible != null && (
                    <span className="text-atlas-muted"> · {grades[selected.id].score}/{grades[selected.id].points_possible}</span>
                  )}
                </div>
              ) : (
                <p className="text-atlas-muted italic text-sm">
                  No grade on file yet{selected.status === "graded" ? " (synced as graded, but no score recorded)" : ""} — enter one if PowerSchool/Schoology hasn't pulled it in.
                </p>
              )}
            </div>

            <div className="flex flex-wrap gap-4 text-xs text-atlas-muted pt-1">
              {selected.points_possible != null && <span>Points: {selected.points_possible}</span>}
              {weightLabel(selected.weight) && <span>Weight: {weightLabel(selected.weight)}</span>}
              {selected.estimated_minutes != null && <span>Est. {selected.estimated_minutes} min</span>}
              {selected.difficulty != null && <span>Difficulty {selected.difficulty}/5</span>}
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
