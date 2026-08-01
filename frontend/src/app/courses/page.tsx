"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Empty, SkeletonGrid, Badge, Ring, gradeTone } from "@/components/ui";
import { Icon } from "@/components/Icon";
import { ColorPicker } from "@/components/ColorPicker";
import { courseColor } from "@/lib/courseColor";
import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { groupCourses as groupCoursesShared } from "@/lib/courseGroups";

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
  color?: string | null;
  semester?: string;
  linked_course_id?: string | null;
  sort_order: number;
  is_active?: boolean;
}

interface CourseGroup {
  key: string;
  primary: Course;
  members: Course[];
}

const SEMESTER_SHORT: Record<string, string> = { s1: "S1", s2: "S2" };

// A class split into linked semester rows (e.g. an HN-weighted S1 feeding an
// AP-weighted S2) is still one class — group those rows into a single card
// instead of showing duplicates on the list (see lib/courseGroups, shared
// with the Documents page's own class dividers). The detail page already
// shows the S1/S2 breakdown for whichever row you open.
function groupCourses(list: Course[]): CourseGroup[] {
  return groupCoursesShared(list);
}

// A group counts as completed only once every semester half has ended —
// Schoology flips `is_active` false per-section as each grading period
// closes, so a split course (e.g. HN Prep Lab S1 -> AP S2) stays "current"
// until its last half wraps up. Rows synced before the column existed have
// no value yet, so undefined defaults to active.
function isGroupActive(g: CourseGroup): boolean {
  return g.members.some((m) => m.is_active !== false);
}

const emptyForm = {
  name: "",
  code: "",
  subject: "",
  course_level: "regular" as CourseLevel,
  has_hn_prep_lab: false,
  has_ap_prep_lab: false,
  color: null as string | null,
};

export default function CoursesPage() {
  const router = useRouter();
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [open, setOpen] = useState(false);
  const dragIndex = useRef<number | null>(null);
  const didDragRef = useRef(false);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);

  async function load() {
    try {
      setCourses(await apiGet("/courses"));
      setLoadError(null);
    } catch (e: any) {
      setLoadError(e.message);
    }
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

    const reorderedGroups = [...activeGroups];
    const [moved] = reorderedGroups.splice(from, 1);
    reorderedGroups.splice(i, 0, moved);
    setCourses(reorderedGroups.flatMap((g) => g.members));

    // Every member of a group (both semester halves) gets the same
    // sort_order so they stay adjacent and in the right place next load.
    await Promise.all(
      reorderedGroups.flatMap((g, idx) =>
        g.members.map((c) =>
          c.sort_order === idx ? null : apiPatch(`/courses/${c.id}`, { sort_order: idx })
        )
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

  const allGroups = courses ? groupCourses(courses) : [];
  const activeGroups = allGroups.filter(isGroupActive);
  const completedGroups = allGroups
    .filter((g) => !isGroupActive(g))
    .sort((a, b) => a.primary.name.localeCompare(b.primary.name));

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

          <div className="md:col-span-4">
            <label className="label">Calendar color</label>
            <ColorPicker courseId="" value={form.color} onChange={(c) => setForm({ ...form, color: c })} />
          </div>

          <button className="btn-primary md:col-span-4">Save course</button>
        </form>
      )}

      {!courses && !loadError && <SkeletonGrid items={6} />}
      {loadError && !courses && (
        <div className="card border-atlas-bad/40 text-sm mb-6">
          <div className="font-medium text-atlas-bad">Couldn't load your classes</div>
          <div className="text-atlas-muted mt-1">{loadError}</div>
          <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
        </div>
      )}
      {courses && !courses.length && <Empty>No courses yet. Add your first one.</Empty>}
      {courses && courses.length > 0 && activeGroups.length === 0 && (
        <Empty>No current classes — see completed classes below.</Empty>
      )}
      {activeGroups.length > 0 && (
        <p className="text-xs text-atlas-muted mb-3">Click a card to open it · drag to reorder.</p>
      )}
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {activeGroups.map((g, i) => {
          const c = g.primary;
          const isSplit = g.members.length > 1;
          const levels = Array.from(new Set(g.members.map((m) => m.course_level)));
          return (
            <div
              key={g.key}
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
                  <div className="font-medium flex items-center gap-2">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: courseColor(c.id, c.color) }}
                    />
                    {c.name}
                  </div>
                  <div className="text-xs text-atlas-muted">{c.code || c.subject || "—"}</div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  {isSplit ? (
                    <>
                      <Badge tone="accent">S1 · S2</Badge>
                      {levels.length > 1 ? (
                        <Badge tone="accent">
                          {levels.map((l) => LEVEL_BADGE[l as CourseLevel] ?? l).join(" → ")}
                        </Badge>
                      ) : (
                        levels[0] !== "regular" && (
                          <Badge tone="accent">{LEVEL_BADGE[levels[0] as CourseLevel]}</Badge>
                        )
                      )}
                    </>
                  ) : (
                    <>
                      {c.semester && c.semester !== "full_year" && (
                        <Badge tone="accent">{SEMESTER_SHORT[c.semester] ?? c.semester}</Badge>
                      )}
                      {c.course_level !== "regular" && (
                        <Badge tone="accent">{LEVEL_BADGE[c.course_level]}</Badge>
                      )}
                      {c.has_hn_prep_lab && <Badge tone="warn">HN Prep Lab</Badge>}
                      {c.has_ap_prep_lab && <Badge tone="warn">AP Prep Lab</Badge>}
                    </>
                  )}
                </div>
              </div>
              <div className="mt-4 flex items-center gap-3">
                <Ring
                  percent={c.current_grade ?? 0}
                  tone={c.current_grade != null ? gradeTone(c.current_grade) : "default"}
                  label={c.current_grade != null ? undefined : "—"}
                />
                <div className="min-w-0">
                  <div className="text-xs text-atlas-muted">Current grade</div>
                  <div className="text-sm font-medium">
                    {c.current_grade != null ? `${c.current_grade}% ${c.current_letter ?? ""}` : "Not graded yet"}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {completedGroups.length > 0 && (
        <section className="mt-10 pt-6 border-t border-atlas-border">
          <button
            className="flex items-center gap-2 text-sm font-semibold text-atlas-muted hover:text-atlas-text mb-3"
            onClick={() => setShowCompleted((s) => !s)}
          >
            <span className="transition-transform inline-block" style={{ transform: showCompleted ? "rotate(90deg)" : "none" }}>
              <Icon name="chevronRight" className="w-4 h-4" />
            </span>
            Completed classes ({completedGroups.length})
          </button>
          {showCompleted && (
            <div className="rounded-xl border border-atlas-border divide-y divide-atlas-border overflow-hidden">
              {completedGroups.map((g) => {
                const c = g.primary;
                return (
                  <div
                    key={g.key}
                    onClick={() => onCardClick(c.id)}
                    className="flex items-center justify-between gap-4 px-4 py-2.5 cursor-pointer hover:bg-atlas-panel2 transition-colors"
                  >
                    <div className="min-w-0">
                      <span className="text-sm text-atlas-muted">{c.name}</span>
                      <span className="text-xs text-atlas-muted/70 ml-2">{c.code || c.subject || ""}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-sm text-atlas-muted tabular-nums">
                        {c.current_grade != null ? `${c.current_grade}% ${c.current_letter ?? ""}` : "—"}
                      </span>
                      <Badge tone="default">Completed</Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}
    </AppShell>
  );
}
