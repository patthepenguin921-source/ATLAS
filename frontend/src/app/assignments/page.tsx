"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge } from "@/components/ui";
import { apiGet, apiPost, apiPatch } from "@/lib/api";

const CATEGORIES = ["homework", "quiz", "test", "project", "essay", "lab", "other"];
const STATUSES = ["not_started", "in_progress", "submitted", "graded", "missing"];

const statusTone = (s: string) =>
  s === "graded" || s === "submitted" ? "good" : s === "missing" ? "bad" : "default";

export default function AssignmentsPage() {
  const [items, setItems] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({
    title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30,
  });

  async function load() {
    const [a, c] = await Promise.all([apiGet("/assignments"), apiGet("/courses")]);
    setItems(a);
    setCourses(c);
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const body = { ...form };
    if (body.due_date) body.due_date = new Date(body.due_date).toISOString();
    if (!body.course_id) delete body.course_id;
    await apiPost("/assignments", body);
    setForm({ title: "", course_id: "", category: "homework", due_date: "", estimated_minutes: 30 });
    setOpen(false);
    load();
  }

  async function setStatus(id: string, status: string) {
    await apiPatch(`/assignments/${id}`, {
      status,
      ...(status === "submitted" ? { submitted_at: new Date().toISOString() } : {}),
    });
    load();
  }

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "—";

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
          <button className="btn-primary md:col-span-5">Save assignment</button>
        </form>
      )}

      {!items && <Loading />}
      {items && !items.length && <Empty>No assignments yet.</Empty>}
      <div className="space-y-2">
        {items?.map((a) => (
          <div key={a.id} className="card flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="font-medium truncate">{a.title}</div>
              <div className="text-xs text-atlas-muted">
                {courseName(a.course_id)} · {a.category}
                {a.due_date && ` · due ${new Date(a.due_date).toLocaleString()}`}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
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
    </AppShell>
  );
}
