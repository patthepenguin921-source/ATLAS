"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, Modal, RiskBadge, SkeletonList } from "@/components/ui";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { formatCalendarDate } from "@/lib/date";

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

export default function AssignmentsPage() {
  const [items, setItems] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [risk, setRisk] = useState<Record<string, { risk_level: string; risk_score: number }>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const [editing, setEditing] = useState(false);
  const [detail, setDetail] = useState<{
    description: string; notes: string; weight: string; difficulty: string; points_possible: string;
  }>({ description: "", notes: "", weight: "", difficulty: "", points_possible: "" });
  const [form, setForm] = useState<any>({
    title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30,
    description: "", notes: "", weight: "",
  });

  async function load() {
    try {
      const [a, c, r] = await Promise.all([
        apiGet("/assignments"), apiGet("/courses"), apiGet("/analytics/at-risk?limit=100"),
      ]);
      setItems(a);
      setCourses(c);
      setRisk(Object.fromEntries((r ?? []).map((x: any) => [x.id, x])));
      setLoadError(null);
    } catch (e: any) {
      // Without this, a failed request left `items` at its initial `null`
      // forever -- the skeleton below just spins with no explanation.
      setLoadError(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const body = { ...form };
    if (body.due_date) body.due_date = new Date(body.due_date).toISOString();
    if (!body.course_id) delete body.course_id;
    if (body.weight) body.weight = Number(body.weight);
    else delete body.weight;
    await apiPost("/assignments", body);
    setForm({ title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30,
      description: "", notes: "", weight: "" });
    setOpen(false);
    load();
  }

  function openDetail(a: any) {
    setSelected(a);
    setEditing(false);
    setDetail({
      description: a.description ?? "", notes: a.notes ?? "",
      weight: a.weight != null ? String(a.weight) : "",
      difficulty: a.difficulty != null ? String(a.difficulty) : "",
      points_possible: a.points_possible != null ? String(a.points_possible) : "",
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
    };
    await apiPatch(`/assignments/${selected.id}`, patch);
    setSelected((s: any) => (s ? { ...s, ...patch } : s));
    setEditing(false);
    load();
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

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "—";
  const weightLabel = (w: number | null | undefined) =>
    w === 0.3 ? "Minor (30%)" : w === 0.7 ? "Major (70%)" : w != null ? `${Math.round(w * 100)}%` : null;

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

      {!items && !loadError && <SkeletonList rows={4} />}
      {loadError && !items && (
        <div className="card border-atlas-bad/40 text-sm mb-6">
          <div className="font-medium text-atlas-bad">Couldn't load your assignments</div>
          <div className="text-atlas-muted mt-1">{loadError}</div>
          <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
        </div>
      )}
      {items && !items.length && <Empty>No assignments yet.</Empty>}
      <div className="space-y-2">
        {items?.map((a) => (
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
