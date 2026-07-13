"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, gradeTone } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

export default function CoursesPage() {
  const [courses, setCourses] = useState<any[] | null>(null);
  const [form, setForm] = useState({ name: "", code: "", subject: "", is_ap: false });
  const [open, setOpen] = useState(false);

  async function load() {
    setCourses(await apiGet("/courses"));
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    await apiPost("/courses", form);
    setForm({ name: "", code: "", subject: "", is_ap: false });
    setOpen(false);
    load();
  }

  return (
    <AppShell
      title="Courses"
      subtitle="Every class Atlas is tracking"
      actions={
        <button className="btn-primary" onClick={() => setOpen((o) => !o)}>
          {open ? "Close" : "Add course"}
        </button>
      }
    >
      {open && (
        <form onSubmit={add} className="card mb-6 grid md:grid-cols-4 gap-3 items-end">
          <div className="md:col-span-2">
            <label className="label">Name</label>
            <input className="input" required value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="AP Biology" />
          </div>
          <div>
            <label className="label">Code</label>
            <input className="input" value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="BIO-AP" />
          </div>
          <div>
            <label className="label">Subject</label>
            <input className="input" value={form.subject}
              onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="Science" />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_ap}
              onChange={(e) => setForm({ ...form, is_ap: e.target.checked })} />
            AP course
          </label>
          <button className="btn-primary md:col-span-4">Save course</button>
        </form>
      )}

      {!courses && <Loading />}
      {courses && !courses.length && <Empty>No courses yet. Add your first one.</Empty>}
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {courses?.map((c) => (
          <div key={c.id} className="card card-hover">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-xs text-atlas-muted">{c.code || c.subject || "—"}</div>
              </div>
              {c.is_ap && <Badge tone="accent">AP</Badge>}
            </div>
            <div className="mt-4 flex items-center justify-between">
              <span className="text-xs text-atlas-muted">Current grade</span>
              <span className={`text-lg font-semibold ${
                gradeTone(c.current_grade) === "good" ? "text-atlas-good"
                : gradeTone(c.current_grade) === "warn" ? "text-atlas-warn"
                : gradeTone(c.current_grade) === "bad" ? "text-atlas-bad" : ""}`}>
                {c.current_grade != null ? `${c.current_grade}% ${c.current_letter ?? ""}` : "—"}
              </span>
            </div>
          </div>
        ))}
      </div>
    </AppShell>
  );
}
