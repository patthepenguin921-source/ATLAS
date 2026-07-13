"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, gradeTone } from "@/components/ui";
import { apiGet, apiPatch, apiPost } from "@/lib/api";

type CourseLevel = "regular" | "honors" | "ap" | "dual_enrollment" | "ib";

const LEVEL_OPTIONS: { value: CourseLevel; label: string }[] = [
  { value: "regular", label: "Regular" },
  { value: "honors", label: "Honors" },
  { value: "ap", label: "AP" },
  { value: "dual_enrollment", label: "Dual Enrollment" },
  { value: "ib", label: "IB" },
];

const LEVEL_BADGE: Record<CourseLevel, string> = {
  regular: "Regular",
  honors: "Honors",
  ap: "AP",
  dual_enrollment: "Dual Enrollment",
  ib: "IB",
};

interface Course {
  id: string;
  name: string;
  code?: string | null;
  subject?: string | null;
  course_level: CourseLevel;
  has_hn_prep_lab: boolean;
  has_ap_prep_lab: boolean;
  current_grade?: number | null;
  current_letter?: string | null;
  sort_order: number;
}

const emptyForm = {
  name: "",
  code: "",
  subject: "",
  course_level: "regular" as CourseLevel,
  has_hn_prep_lab: false,
  has_ap_prep_lab: false,
};

export default function CoursesPage() {
  const router = useRouter();
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [open, setOpen] = useState(false);
  const dragIndex = useRef<number | null>(null);
  const didDragRef = useRef(false);
  const [overIndex, setOverIndex] = useState<number | null>(null);

  async function load() {
    setCourses(await apiGet("/courses"));
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    await apiPost("/courses", { ...form, sort_order: courses?.length ?? 0 });
    setForm(emptyForm);
    setOpen(false);
    load();
  }

  function onDragStart(i: number) {
    dragIndex.current = i;
    didDragRef.current = true;
  }

  function onDragOver(e: React.DragEvent, i: number) {
    e.preventDefault();
    setOverIndex(i);
  }

  async function onDrop(i: number) {
    const from = dragIndex.current;
    dragIndex.current = null;
    setOverIndex(null);
    // Clear the drag flag after this tick so a trailing click (if the browser
    // fires one) is still suppressed, but future plain clicks navigate again.
    setTimeout(() => {
      didDragRef.current = false;
    }, 0);
    if (from === null || from === i || !courses) return;

    const reordered = [...courses];
    const [moved] = reordered.splice(from, 1);
    reordered.splice(i, 0, moved);
    setCourses(reordered);

    await Promise.all(
      reordered.map((c, idx) =>
        idx === c.sort_order ? null : apiPatch(`/courses/${c.id}`, { sort_order: idx })
      )
    );
    load();
  }

  function onCardClick(id: string) {
    if (didDragRef.current) {
      didDragRef.current = false;
      return;
    }
    router.push(`/courses/${id}`);
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

          <div>
            <label className="label">Course level</label>
            <select className="input" value={form.course_level}
              onChange={(e) => setForm({ ...form, course_level: e.target.value as CourseLevel })}>
              {LEVEL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          <div className="md:col-span-3 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.has_hn_prep_lab}
                onChange={(e) => setForm({ ...form, has_hn_prep_lab: e.target.checked })} />
              HN Prep Lab (year-long, 5.5 weighted)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.has_ap_prep_lab}
                onChange={(e) => setForm({ ...form, has_ap_prep_lab: e.target.checked })} />
              AP Prep Lab (year-long, 6.0 weighted)
            </label>
          </div>

          <button className="btn-primary md:col-span-4">Save course</button>
        </form>
      )}

      {!courses && <Loading />}
      {courses && !courses.length && <Empty>No courses yet. Add your first one.</Empty>}
      {courses && courses.length > 0 && (
        <p className="text-xs text-atlas-muted mb-3">Click a card to open it · drag to reorder.</p>
      )}
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {courses?.map((c, i) => (
          <div
            key={c.id}
            draggable
            onDragStart={() => onDragStart(i)}
            onDragOver={(e) => onDragOver(e, i)}
            onDrop={() => onDrop(i)}
            onClick={() => onCardClick(c.id)}
            className={`card card-hover cursor-grab active:cursor-grabbing ${
              overIndex === i ? "ring-2 ring-atlas-accent2" : ""
            }`}
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-xs text-atlas-muted">{c.code || c.subject || "—"}</div>
              </div>
              <div className="flex flex-col items-end gap-1">
                {c.course_level !== "regular" && (
                  <Badge tone="accent">{LEVEL_BADGE[c.course_level]}</Badge>
                )}
                {c.has_hn_prep_lab && <Badge tone="warn">HN Prep Lab</Badge>}
                {c.has_ap_prep_lab && <Badge tone="warn">AP Prep Lab</Badge>}
              </div>
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
