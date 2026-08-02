"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Stat, Section, Empty, SkeletonStats, SkeletonList, Badge, Modal, gradeTone } from "@/components/ui";
import { ColorPicker } from "@/components/ColorPicker";
import { FolderPane } from "@/components/FolderPane";
import { CourseAssistant } from "@/components/CourseAssistant";
import { apiGet, apiPatch, apiPost, apiDelete } from "@/lib/api";
import { formatCalendarDate } from "@/lib/date";
import { buildFolderOptions } from "@/lib/folderOptions";

const ASSIGNMENT_CATEGORIES = [
  "homework", "classwork", "quiz", "test", "exam", "project", "essay",
  "lab", "discussion", "presentation", "reading", "participation", "other",
];
const ASSIGNMENT_STATUSES = ["not_started", "in_progress", "submitted", "graded", "missing"];
const ASSIGNMENT_WEIGHT_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Not set" },
  { value: "0.3", label: "Minor (30%)" },
  { value: "0.7", label: "Major (70%)" },
];
const ASSIGNMENT_DIFFICULTY_OPTIONS = [1, 2, 3, 4, 5];
const ASSIGNMENT_RISK_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Auto (computed)" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extreme", label: "Extreme" },
];

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

const SEMESTER_LABEL: Record<string, string> = {
  full_year: "Full year",
  s1: "Semester 1",
  s2: "Semester 2",
};

const assignmentStatusTone = (s: string) =>
  s === "graded" || s === "submitted" ? "good" : s === "missing" || s === "late" ? "bad" : "default";

interface EditForm {
  name: string;
  code: string;
  subject: string;
  course_level: CourseLevel;
  has_hn_prep_lab: boolean;
  has_ap_prep_lab: boolean;
  credit_hours: number;
  period: string;
  room: string;
  teacher_id: string;
  term_id: string;
  color: string | null;
}

export default function CourseDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id as string;
  const [deleteStep, setDeleteStep] = useState(0);

  const [course, setCourse] = useState<any>(null);
  const [teachers, setTeachers] = useState<any[]>([]);
  const [terms, setTerms] = useState<any[]>([]);
  const [assignments, setAssignments] = useState<any[]>([]);
  const [grades, setGrades] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [announcements, setAnnouncements] = useState<any[]>([]);
  const [mistakes, setMistakes] = useState<any[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);

  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<EditForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [semesters, setSemesters] = useState<any[]>([]);
  const [splitting, setSplitting] = useState(false);
  const [newTeacher, setNewTeacher] = useState("");
  const [newTerm, setNewTerm] = useState("");
  const [folders, setFolders] = useState<any[]>([]);

  // Assignments: click-to-view/edit (item 12) + "Add assignment" (also 12).
  const [selectedAssignment, setSelectedAssignment] = useState<any | null>(null);
  const [assignEditing, setAssignEditing] = useState(false);
  const [assignDetail, setAssignDetail] = useState<{
    description: string; notes: string; status: string; category: string; due_date: string;
    weight: string; difficulty: string; points_possible: string; risk_override: string; folder_id: string;
  } | null>(null);
  const [addingAssignment, setAddingAssignment] = useState(false);
  const [newAssignment, setNewAssignment] = useState({
    title: "", category: "homework", due_date: "", weight: "", folder_id: "",
  });

  // Calendar events: create/edit/delete for both "Class schedule" (kind
  // 'class') and "Upcoming events" (everything else) -- item 11.
  const [eventModal, setEventModal] = useState<{ kind: string; editing: any | null } | null>(null);
  const [eventForm, setEventForm] = useState({
    title: "", date: "", time: "", all_day: true, location: "", description: "",
  });
  const [savingEvent, setSavingEvent] = useState(false);

  // Minimize/expand for the two sections that take up the most vertical
  // space (item 13) -- collapsed state isn't persisted, just a per-visit toggle.
  const [scheduleOpen, setScheduleOpen] = useState(true);
  const [eventsOpen, setEventsOpen] = useState(true);

  async function load() {
    try {
      const [c, t, tm, a, g, e, an, mi, ss, sem, fo] = await Promise.all([
        apiGet(`/courses/${id}`),
        apiGet("/teachers"),
        apiGet("/terms"),
        apiGet(`/assignments?course_id=${id}`),
        apiGet(`/grades?course_id=${id}`),
        apiGet(`/calendar?course_id=${id}`),
        apiGet(`/announcements?course_id=${id}`),
        apiGet(`/mistakes?course_id=${id}`),
        apiGet(`/study-sessions?course_id=${id}`),
        apiGet(`/courses/${id}/semesters`).catch(() => []),
        apiGet(`/folders?course_id=${id}`).catch(() => []),
      ]);
      setCourse(c);
      setTeachers(t ?? []);
      setTerms(tm ?? []);
      setSemesters(sem ?? []);
      setAssignments(a ?? []);
      setGrades(g ?? []);
      setEvents(e ?? []);
      setAnnouncements(an ?? []);
      setMistakes(mi ?? []);
      setSessions(ss ?? []);
      setFolders(fo ?? []);
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => {
    if (id) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function startEdit() {
    setForm({
      name: course.name ?? "",
      code: course.code ?? "",
      subject: course.subject ?? "",
      course_level: (course.course_level ?? "regular") as CourseLevel,
      has_hn_prep_lab: !!course.has_hn_prep_lab,
      has_ap_prep_lab: !!course.has_ap_prep_lab,
      credit_hours: course.credit_hours ?? 1.0,
      period: course.period ?? "",
      room: course.room ?? "",
      teacher_id: course.teacher_id ?? "",
      term_id: course.term_id ?? "",
      color: course.color ?? null,
    });
    setEditing(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    const body: any = { ...form, credit_hours: Number(form.credit_hours) || 1.0 };
    body.teacher_id = body.teacher_id || null;
    body.term_id = body.term_id || null;
    await apiPatch(`/courses/${id}`, body);
    setEditing(false);
    await load();
  }

  async function addTeacher() {
    const name = newTeacher.trim();
    if (!name || !form) return;
    const created = await apiPost("/teachers", { name });
    setTeachers((prev) => [...prev, created]);
    setForm({ ...form, teacher_id: created.id });
    setNewTeacher("");
  }

  async function addTerm() {
    const name = newTerm.trim();
    if (!name || !form) return;
    const created = await apiPost("/terms", { name });
    setTerms((prev) => [...prev, created]);
    setForm({ ...form, term_id: created.id });
    setNewTerm("");
  }

  async function deleteCourse() {
    await apiDelete(`/courses/${id}`);
    router.push("/courses");
  }

  async function splitSemesters() {
    setSplitting(true);
    try {
      // S1 keeps the current level as an HN prep lab (5.5); S2 becomes AP (6.0).
      await apiPost(`/courses/${id}/split-semesters`, {
        s1_has_hn_prep_lab: true,
        s2_level: "ap",
      });
      await load();
    } finally {
      setSplitting(false);
    }
  }

  // ---- Assignments: click-to-view/edit + add (item 12) ----
  function openAssignment(a: any) {
    setSelectedAssignment(a);
    setAssignEditing(false);
    setAssignDetail({
      description: a.description ?? "", notes: a.notes ?? "", status: a.status,
      category: a.category, due_date: a.due_date ? a.due_date.slice(0, 16) : "",
      weight: a.weight != null ? String(a.weight) : "",
      difficulty: a.difficulty != null ? String(a.difficulty) : "",
      points_possible: a.points_possible != null ? String(a.points_possible) : "",
      risk_override: a.risk_override ?? "", folder_id: a.folder_id ?? "",
    });
  }

  async function saveAssignment() {
    if (!selectedAssignment || !assignDetail) return;
    const patch: any = {
      description: assignDetail.description, notes: assignDetail.notes,
      status: assignDetail.status, category: assignDetail.category,
      due_date: assignDetail.due_date ? new Date(assignDetail.due_date).toISOString() : null,
      weight: assignDetail.weight ? Number(assignDetail.weight) : null,
      difficulty: assignDetail.difficulty ? Number(assignDetail.difficulty) : null,
      points_possible: assignDetail.points_possible ? Number(assignDetail.points_possible) : null,
      risk_override: assignDetail.risk_override || null,
      folder_id: assignDetail.folder_id || null,
    };
    if (assignDetail.status === "submitted" && selectedAssignment.status !== "submitted") {
      patch.submitted_at = new Date().toISOString();
    }
    await apiPatch(`/assignments/${selectedAssignment.id}`, patch);
    setSelectedAssignment(null);
    setAssignEditing(false);
    await load();
  }

  async function deleteAssignment() {
    if (!selectedAssignment) return;
    await apiDelete(`/assignments/${selectedAssignment.id}`);
    setSelectedAssignment(null);
    await load();
  }

  async function addAssignment(e: React.FormEvent) {
    e.preventDefault();
    const body: any = {
      title: newAssignment.title, course_id: id, category: newAssignment.category,
    };
    if (newAssignment.due_date) body.due_date = new Date(newAssignment.due_date).toISOString();
    if (newAssignment.weight) body.weight = Number(newAssignment.weight);
    if (newAssignment.folder_id) body.folder_id = newAssignment.folder_id;
    await apiPost("/assignments", body);
    setNewAssignment({ title: "", category: "homework", due_date: "", weight: "", folder_id: "" });
    setAddingAssignment(false);
    await load();
  }

  // ---- Calendar events: create/edit/delete (item 11) ----
  function openEventModal(kind: string, editing: any | null) {
    setEventModal({ kind, editing });
    if (editing) {
      const d = new Date(editing.starts_at);
      setEventForm({
        title: editing.title ?? "",
        date: editing.starts_at ? editing.starts_at.slice(0, 10) : "",
        time: editing.all_day ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
        all_day: !!editing.all_day,
        location: editing.location ?? "",
        description: editing.description ?? "",
      });
    } else {
      setEventForm({ title: "", date: "", time: "", all_day: kind !== "class", location: "", description: "" });
    }
  }

  async function saveEvent() {
    if (!eventModal || !eventForm.title.trim() || !eventForm.date) return;
    setSavingEvent(true);
    try {
      const body: any = {
        title: eventForm.title.trim(),
        course_id: id,
        kind: eventModal.kind,
        all_day: eventForm.all_day,
        starts_at: eventForm.all_day ? eventForm.date : `${eventForm.date}T${eventForm.time || "00:00"}:00`,
        location: eventForm.location || null,
        description: eventForm.description || null,
      };
      if (eventModal.editing) {
        await apiPatch(`/calendar/${eventModal.editing.id}`, body);
      } else {
        await apiPost("/calendar", body);
      }
      setEventModal(null);
      await load();
    } finally {
      setSavingEvent(false);
    }
  }

  async function deleteEvent() {
    if (!eventModal?.editing) return;
    await apiDelete(`/calendar/${eventModal.editing.id}`);
    setEventModal(null);
    await load();
  }

  if (error) {
    return (
      <AppShell title="Course" subtitle="Something went wrong">
        <div className="card border-atlas-bad/40 text-atlas-bad text-sm">{error}</div>
      </AppShell>
    );
  }
  if (!course) {
    return (
      <AppShell title="Course">
        <SkeletonStats count={3} />
        <div className="mt-6">
          <SkeletonList rows={4} />
        </div>
      </AppShell>
    );
  }

  const level = (course.course_level ?? "regular") as CourseLevel;
  const currentSemester = (course.semester ?? "full_year") as string;
  const isSplit = semesters.length > 1;

  // "Class schedule" is the day-by-day rundown Atlas parses out of an "at a
  // glance" document — whether it arrived via the Schoology integration or
  // was uploaded directly (see app.services.schedule_extraction) — kept
  // separate from "Upcoming events" below, which mixes in due dates/exams/
  // other calendar kinds. `otherEvents` excludes kind:'class' rows so they
  // don't also show up duplicated in "Upcoming events".
  const classDays = events
    .filter((ev) => ev.kind === "class")
    .slice()
    .sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime());
  const otherEvents = events.filter((ev) => ev.kind !== "class");

  return (
    <AppShell
      title={course.name}
      subtitle={[course.code, course.subject].filter(Boolean).join(" · ") || "Course dashboard"}
      actions={
        <>
          <Link href="/courses" className="btn-ghost">Back to courses</Link>
          <button className="btn-primary" onClick={() => (editing ? setEditing(false) : startEdit())}>
            {editing ? "Close" : "Settings"}
          </button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 mb-6">
        {course.is_active === false && <Badge tone="default">Completed</Badge>}
        {level !== "regular" && <Badge tone="accent">{LEVEL_BADGE[level]}</Badge>}
        {currentSemester !== "full_year" && (
          <Badge tone="accent">{SEMESTER_LABEL[currentSemester]}</Badge>
        )}
        {course.has_hn_prep_lab && <Badge tone="warn">HN Prep Lab</Badge>}
        {course.has_ap_prep_lab && <Badge tone="warn">AP Prep Lab</Badge>}

        {isSplit ? (
          <div className="flex items-center gap-1.5 ml-auto">
            <span className="text-xs text-atlas-muted">Semesters:</span>
            {semesters.map((s) => (
              <Link
                key={s.id}
                href={`/courses/${s.id}`}
                className={`pill hover:border-atlas-accent/50 ${
                  s.id === id ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"
                }`}
              >
                {SEMESTER_LABEL[s.semester] ?? s.semester} · {LEVEL_BADGE[s.course_level as CourseLevel] ?? s.course_level}
              </Link>
            ))}
          </div>
        ) : (
          <button
            className="btn-ghost ml-auto text-xs py-1.5"
            onClick={splitSemesters}
            disabled={splitting}
            title="Split this class into two linked semester courses (e.g. HN 5.5 → AP 6.0)"
          >
            {splitting ? "Splitting…" : "Split into semesters"}
          </button>
        )}
      </div>

      {editing && form && (
        <form onSubmit={save} className="card mb-6 grid md:grid-cols-4 gap-3 items-end">
          <div className="md:col-span-2">
            <label className="label">Name</label>
            <input className="input" required value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <label className="label">Code</label>
            <input className="input" value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })} />
          </div>
          <div>
            <label className="label">Subject</label>
            <input className="input" value={form.subject}
              onChange={(e) => setForm({ ...form, subject: e.target.value })} />
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
          <div>
            <label className="label">Credit hours</label>
            <input className="input" type="number" step="0.5" min="0" value={form.credit_hours}
              onChange={(e) => setForm({ ...form, credit_hours: Number(e.target.value) })} />
          </div>
          <div>
            <label className="label">Period</label>
            <input className="input" value={form.period}
              onChange={(e) => setForm({ ...form, period: e.target.value })} />
          </div>
          <div>
            <label className="label">Room</label>
            <input className="input" value={form.room}
              onChange={(e) => setForm({ ...form, room: e.target.value })} />
          </div>

          <div>
            <label className="label">Teacher</label>
            <select className="input" value={form.teacher_id}
              onChange={(e) => setForm({ ...form, teacher_id: e.target.value })}>
              <option value="">—</option>
              {teachers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <div className="flex gap-1.5 mt-1.5">
              <input className="input !py-1 text-xs" placeholder="Add a new teacher…"
                value={newTeacher} onChange={(e) => setNewTeacher(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTeacher(); } }} />
              <button type="button" className="btn-ghost text-xs py-1 shrink-0"
                onClick={addTeacher} disabled={!newTeacher.trim()}>Add</button>
            </div>
          </div>
          <div>
            <label className="label">Term</label>
            <select className="input" value={form.term_id}
              onChange={(e) => setForm({ ...form, term_id: e.target.value })}>
              <option value="">—</option>
              {terms.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <div className="flex gap-1.5 mt-1.5">
              <input className="input !py-1 text-xs" placeholder="Add a new term…"
                value={newTerm} onChange={(e) => setNewTerm(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTerm(); } }} />
              <button type="button" className="btn-ghost text-xs py-1 shrink-0"
                onClick={addTerm} disabled={!newTerm.trim()}>Add</button>
            </div>
          </div>

          <div className="md:col-span-2 flex flex-wrap items-center gap-4">
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
            <ColorPicker courseId={id} value={form.color} onChange={(c) => setForm({ ...form, color: c })} />
          </div>

          <button className="btn-primary md:col-span-4">Save changes</button>

          {/* Danger zone — deleting a course needs two explicit confirmations. */}
          <div className="md:col-span-4 mt-2 pt-4 border-t border-atlas-border">
            <div className="text-xs uppercase text-atlas-muted mb-2">Danger zone</div>
            {deleteStep === 0 && (
              <button type="button" className="btn-ghost text-atlas-bad hover:!border-atlas-bad/50"
                onClick={() => setDeleteStep(1)}>
                Delete course
              </button>
            )}
            {deleteStep === 1 && (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-atlas-muted">
                  Delete <span className="text-atlas-text font-medium">{course.name}</span> and all its data?
                </span>
                <button type="button" className="btn-ghost" onClick={() => setDeleteStep(0)}>Cancel</button>
                <button type="button" className="btn-ghost text-atlas-bad hover:!border-atlas-bad/50"
                  onClick={() => setDeleteStep(2)}>Yes, continue</button>
              </div>
            )}
            {deleteStep === 2 && (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-atlas-bad font-medium">
                  This can't be undone. Confirm permanent delete?
                </span>
                <button type="button" className="btn-ghost" onClick={() => setDeleteStep(0)}>Cancel</button>
                <button type="button" className="btn-primary !bg-atlas-bad hover:!brightness-110"
                  onClick={deleteCourse}>Delete permanently</button>
              </div>
            )}
          </div>
        </form>
      )}

      <div className="grid grid-cols-2 gap-4 mb-8">
        <Stat
          label="Current grade"
          value={course.current_grade != null ? `${course.current_grade}% ${course.current_letter ?? ""}` : "—"}
          tone={gradeTone(course.current_grade)}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <Section
          title="Assignments"
          action={
            <button className="text-xs text-atlas-accent hover:underline" onClick={() => setAddingAssignment((o) => !o)}>
              {addingAssignment ? "Cancel" : "+ Add"}
            </button>
          }
        >
          {addingAssignment && (
            <form onSubmit={addAssignment} className="card mb-3 space-y-2">
              <input className="input" required placeholder="Title" value={newAssignment.title}
                onChange={(e) => setNewAssignment({ ...newAssignment, title: e.target.value })} />
              <div className="grid grid-cols-2 gap-2">
                <select className="input text-sm" value={newAssignment.category}
                  onChange={(e) => setNewAssignment({ ...newAssignment, category: e.target.value })}>
                  {ASSIGNMENT_CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                </select>
                <input className="input text-sm" type="datetime-local" value={newAssignment.due_date}
                  onChange={(e) => setNewAssignment({ ...newAssignment, due_date: e.target.value })} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <select className="input text-sm" value={newAssignment.weight}
                  onChange={(e) => setNewAssignment({ ...newAssignment, weight: e.target.value })}>
                  {ASSIGNMENT_WEIGHT_OPTIONS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
                </select>
                <select className="input text-sm" value={newAssignment.folder_id}
                  disabled={!buildFolderOptions(folders).length}
                  onChange={(e) => setNewAssignment({ ...newAssignment, folder_id: e.target.value })}>
                  <option value="">Not categorized</option>
                  {buildFolderOptions(folders).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
              </div>
              <button className="btn-primary text-sm">Save assignment</button>
            </form>
          )}
          {assignments.length ? (
            <div className="space-y-2">
              {assignments.map((a) => (
                <div
                  key={a.id}
                  className="card card-hover cursor-pointer flex items-center justify-between gap-4"
                  onClick={() => openAssignment(a)}
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{a.title}</div>
                    <div className="text-xs text-atlas-muted">
                      {a.category}
                      {a.due_date && ` · due ${formatCalendarDate(a.due_date)}`}
                    </div>
                  </div>
                  <Badge tone={assignmentStatusTone(a.status) as any}>{a.status?.replace("_", " ")}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No assignments recorded for this course.</Empty>
          )}
        </Section>

        <Section title="Grades">
          {grades.length ? (
            <div className="space-y-2">
              {grades.map((g) => (
                <div key={g.id} className="card flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">
                      {g.percentage != null ? `${g.percentage}%` : "—"} {g.letter ? `(${g.letter})` : ""}
                    </div>
                    {g.teacher_comment && (
                      <div className="text-xs text-atlas-muted truncate">&ldquo;{g.teacher_comment}&rdquo;</div>
                    )}
                  </div>
                  <span className="text-xs text-atlas-muted shrink-0">
                    {g.graded_at ? new Date(g.graded_at).toLocaleDateString() : "—"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No grades recorded yet.</Empty>
          )}
        </Section>
      </div>

      <Section
        title="Class schedule"
        collapsible
        open={scheduleOpen}
        onToggle={setScheduleOpen}
        action={
          <button
            className="text-xs text-atlas-accent hover:underline"
            onClick={(e) => { e.stopPropagation(); openEventModal("class", null); }}
          >
            + Add
          </button>
        }
      >
        {classDays.length ? (
          <div className="space-y-2">
            {classDays.map((ev) => (
              <div
                key={ev.id}
                className="card card-hover cursor-pointer flex items-center justify-between gap-4"
                onClick={() => openEventModal("class", ev)}
              >
                <div className="min-w-0">
                  <div className="text-xs text-atlas-muted">
                    {formatCalendarDate(ev.starts_at, { weekday: "long", month: "short", day: "numeric" })}
                  </div>
                  <div className="text-sm font-medium truncate">{ev.title}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No day-by-day schedule found yet — Atlas builds this from a "Week/Unit at a Glance" document, or add one yourself.</Empty>
        )}
      </Section>

      <Section
        title="Upcoming events"
        collapsible
        open={eventsOpen}
        onToggle={setEventsOpen}
        action={
          <button
            className="text-xs text-atlas-accent hover:underline"
            onClick={(e) => { e.stopPropagation(); openEventModal("event", null); }}
          >
            + Add
          </button>
        }
      >
        {otherEvents.length ? (
          <div className="space-y-2">
            {otherEvents.map((ev) => (
              <div
                key={ev.id}
                className="card card-hover cursor-pointer flex items-center justify-between gap-4"
                onClick={() => openEventModal(ev.kind || "event", ev)}
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{ev.title}</div>
                  <div className="text-xs text-atlas-muted">{ev.kind}</div>
                </div>
                <span className="text-xs text-atlas-muted shrink-0">
                  {ev.starts_at
                    ? ev.all_day
                      ? formatCalendarDate(ev.starts_at)
                      : new Date(ev.starts_at).toLocaleString()
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>Nothing on the calendar for this course.</Empty>
        )}
      </Section>

      <Section title="Files & folders">
        <FolderPane
          courseId={semesters.length > 1 ? semesters.map((s) => s.id).join(",") : id}
          uploadCourseId={id}
        />
      </Section>

      <Section title="Assistant">
        <CourseAssistant courseId={id} courseName={course.name} />
      </Section>

      <div className="grid md:grid-cols-2 gap-6">
        <Section title="Announcements">
          {announcements.length ? (
            <div className="space-y-2">
              {announcements.map((an) => (
                <div key={an.id} className="card">
                  <div className="text-sm font-medium">{an.title || "Announcement"}</div>
                  {an.body && <div className="text-xs text-atlas-muted mt-1 line-clamp-2">{an.body}</div>}
                </div>
              ))}
            </div>
          ) : (
            <Empty>No announcements for this course.</Empty>
          )}
        </Section>

        <Section title="Mistakes & patterns">
          {mistakes.length ? (
            <div className="space-y-2">
              {mistakes.map((m) => (
                <div key={m.id} className="card flex items-center justify-between gap-4">
                  <div className="min-w-0 text-sm truncate">{m.description}</div>
                  <Badge tone={m.resolved ? "good" : "warn"}>{m.resolved ? "resolved" : m.mistake_type || "open"}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No recorded mistakes for this course.</Empty>
          )}
        </Section>
      </div>

      <Section title="Study sessions">
        {sessions.length ? (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div key={s.id} className="card flex items-center justify-between gap-4">
                <span className="text-sm">
                  {s.started_at ? new Date(s.started_at).toLocaleString() : "—"}
                  {s.technique ? ` · ${s.technique}` : ""}
                </span>
                <span className="text-xs text-atlas-muted">{s.duration_minutes ?? 0}m</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No study sessions logged for this course.</Empty>
        )}
      </Section>

      <Modal
        open={!!selectedAssignment}
        onClose={() => setSelectedAssignment(null)}
        title={selectedAssignment?.title}
        footer={
          selectedAssignment && assignDetail && (
            <>
              <button
                className="btn-ghost text-atlas-bad hover:!border-atlas-bad/50 mr-auto"
                onClick={deleteAssignment}
              >
                Delete
              </button>
              {assignEditing ? (
                <>
                  <button className="btn-ghost" onClick={() => setAssignEditing(false)}>Cancel</button>
                  <button className="btn-primary" onClick={saveAssignment}>Save</button>
                </>
              ) : (
                <button className="btn-primary" onClick={() => setAssignEditing(true)}>Edit</button>
              )}
            </>
          )
        }
      >
        {selectedAssignment && assignDetail && (
          <div className="space-y-3 text-sm">
            {assignEditing ? (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs text-atlas-muted">Status</label>
                    <select className="input text-sm" value={assignDetail.status}
                      onChange={(e) => setAssignDetail({ ...assignDetail, status: e.target.value })}>
                      {ASSIGNMENT_STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-atlas-muted">Category</label>
                    <select className="input text-sm" value={assignDetail.category}
                      onChange={(e) => setAssignDetail({ ...assignDetail, category: e.target.value })}>
                      {ASSIGNMENT_CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Due</label>
                  <input className="input text-sm" type="datetime-local" value={assignDetail.due_date}
                    onChange={(e) => setAssignDetail({ ...assignDetail, due_date: e.target.value })} />
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Details & instructions</label>
                  <textarea className="input min-h-[80px]" value={assignDetail.description}
                    onChange={(e) => setAssignDetail({ ...assignDetail, description: e.target.value })} />
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Notes</label>
                  <textarea className="input min-h-[60px]" value={assignDetail.notes}
                    onChange={(e) => setAssignDetail({ ...assignDetail, notes: e.target.value })} />
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-xs text-atlas-muted">Weight</label>
                    <select className="input text-sm" value={assignDetail.weight}
                      onChange={(e) => setAssignDetail({ ...assignDetail, weight: e.target.value })}>
                      {ASSIGNMENT_WEIGHT_OPTIONS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-atlas-muted">Difficulty</label>
                    <select className="input text-sm" value={assignDetail.difficulty}
                      onChange={(e) => setAssignDetail({ ...assignDetail, difficulty: e.target.value })}>
                      <option value="">Not set</option>
                      {ASSIGNMENT_DIFFICULTY_OPTIONS.map((d) => <option key={d} value={d}>{d}/5</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-atlas-muted">Points</label>
                    <input type="number" min={0} className="input text-sm" value={assignDetail.points_possible}
                      onChange={(e) => setAssignDetail({ ...assignDetail, points_possible: e.target.value })} />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Risk level override</label>
                  <select className="input text-sm" value={assignDetail.risk_override}
                    onChange={(e) => setAssignDetail({ ...assignDetail, risk_override: e.target.value })}>
                    {ASSIGNMENT_RISK_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-atlas-muted">Unit</label>
                  <select className="input text-sm" value={assignDetail.folder_id}
                    disabled={!buildFolderOptions(folders).length}
                    onChange={(e) => setAssignDetail({ ...assignDetail, folder_id: e.target.value })}>
                    <option value="">Not categorized</option>
                    {buildFolderOptions(folders).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                </div>
              </>
            ) : (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge tone={assignmentStatusTone(selectedAssignment.status) as any}>
                    {selectedAssignment.status?.replace("_", " ")}
                  </Badge>
                  <Badge>{selectedAssignment.category}</Badge>
                </div>
                {selectedAssignment.due_date && (
                  <div className="text-atlas-muted">Due {formatCalendarDate(selectedAssignment.due_date)}</div>
                )}
                <div>
                  <div className="text-xs uppercase text-atlas-muted mb-1">Details & instructions</div>
                  {selectedAssignment.description ? (
                    <p className="whitespace-pre-wrap">{selectedAssignment.description}</p>
                  ) : (
                    <p className="text-atlas-muted italic">No details yet — tap Edit to add instructions.</p>
                  )}
                </div>
                <div>
                  <div className="text-xs uppercase text-atlas-muted mb-1">Notes</div>
                  {selectedAssignment.notes ? (
                    <p className="whitespace-pre-wrap">{selectedAssignment.notes}</p>
                  ) : (
                    <p className="text-atlas-muted italic">No notes yet.</p>
                  )}
                </div>
                <div className="flex flex-wrap gap-4 text-xs text-atlas-muted pt-1">
                  {selectedAssignment.points_possible != null && <span>Points: {selectedAssignment.points_possible}</span>}
                  {selectedAssignment.difficulty != null && <span>Difficulty {selectedAssignment.difficulty}/5</span>}
                </div>
              </>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={!!eventModal}
        onClose={() => setEventModal(null)}
        title={eventModal?.editing ? "Edit event" : eventModal?.kind === "class" ? "Add class meeting" : "Add event"}
        footer={
          <>
            {eventModal?.editing && (
              <button className="btn-ghost text-atlas-bad hover:!border-atlas-bad/50 mr-auto" onClick={deleteEvent}>
                Delete
              </button>
            )}
            <button className="btn-ghost" onClick={() => setEventModal(null)}>Cancel</button>
            <button className="btn-primary" disabled={savingEvent || !eventForm.title.trim() || !eventForm.date} onClick={saveEvent}>
              {savingEvent ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        {eventModal && (
          <div className="space-y-3 text-sm">
            <div>
              <label className="label">Title</label>
              <input className="input" value={eventForm.title}
                onChange={(e) => setEventForm({ ...eventForm, title: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Date</label>
                <input className="input" type="date" value={eventForm.date}
                  onChange={(e) => setEventForm({ ...eventForm, date: e.target.value })} />
              </div>
              <div>
                <label className="label">Time</label>
                <input className="input" type="time" value={eventForm.time} disabled={eventForm.all_day}
                  onChange={(e) => setEventForm({ ...eventForm, time: e.target.value })} />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={eventForm.all_day}
                onChange={(e) => setEventForm({ ...eventForm, all_day: e.target.checked })} />
              All day
            </label>
            <div>
              <label className="label">Location</label>
              <input className="input" value={eventForm.location}
                onChange={(e) => setEventForm({ ...eventForm, location: e.target.value })} />
            </div>
            <div>
              <label className="label">Description</label>
              <textarea className="input min-h-[60px]" value={eventForm.description}
                onChange={(e) => setEventForm({ ...eventForm, description: e.target.value })} />
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
