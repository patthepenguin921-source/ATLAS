"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Stat, Section, Empty, Loading, Badge, gradeTone } from "@/components/ui";
import { apiGet, apiPatch, apiPost, apiDelete } from "@/lib/api";

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
  const [documents, setDocuments] = useState<any[]>([]);
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

  async function load() {
    try {
      const [c, t, tm, a, g, e, d, an, mi, ss, sem] = await Promise.all([
        apiGet(`/courses/${id}`),
        apiGet("/teachers"),
        apiGet("/terms"),
        apiGet(`/assignments?course_id=${id}`),
        apiGet(`/grades?course_id=${id}`),
        apiGet(`/calendar?course_id=${id}`),
        apiGet(`/documents?course_id=${id}`),
        apiGet(`/announcements?course_id=${id}`),
        apiGet(`/mistakes?course_id=${id}`),
        apiGet(`/study-sessions?course_id=${id}`),
        apiGet(`/courses/${id}/semesters`).catch(() => []),
      ]);
      setCourse(c);
      setTeachers(t ?? []);
      setTerms(tm ?? []);
      setSemesters(sem ?? []);
      setAssignments(a ?? []);
      setGrades(g ?? []);
      setEvents(e ?? []);
      setDocuments(d ?? []);
      setAnnouncements(an ?? []);
      setMistakes(mi ?? []);
      setSessions(ss ?? []);
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
        <Loading />
      </AppShell>
    );
  }

  const level = (course.course_level ?? "regular") as CourseLevel;
  const teacherName = teachers.find((t) => t.id === course.teacher_id)?.name;
  const termName = terms.find((t) => t.id === course.term_id)?.name;
  const currentSemester = (course.semester ?? "full_year") as string;
  const isSplit = semesters.length > 1;

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

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-8">
        <Stat
          label="Current grade"
          value={course.current_grade != null ? `${course.current_grade}% ${course.current_letter ?? ""}` : "—"}
          tone={gradeTone(course.current_grade)}
        />
        <Stat label="Credit hours" value={course.credit_hours ?? "—"} />
        <Stat
          label="Teacher"
          value={teacherName ?? "—"}
          hint={[course.period, course.room].filter(Boolean).join(" · ") || termName || undefined}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <Section title="Assignments">
          {assignments.length ? (
            <div className="space-y-2">
              {assignments.map((a) => (
                <div key={a.id} className="card flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{a.title}</div>
                    <div className="text-xs text-atlas-muted">
                      {a.category}
                      {a.due_date && ` · due ${new Date(a.due_date).toLocaleDateString()}`}
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

      <div className="grid md:grid-cols-2 gap-6">
        <Section title="Upcoming events">
          {events.length ? (
            <div className="space-y-2">
              {events.map((ev) => (
                <div key={ev.id} className="card flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{ev.title}</div>
                    <div className="text-xs text-atlas-muted">{ev.kind}</div>
                  </div>
                  <span className="text-xs text-atlas-muted shrink-0">
                    {ev.starts_at ? new Date(ev.starts_at).toLocaleString() : "—"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>Nothing on the calendar for this course.</Empty>
          )}
        </Section>

        <Section title="Documents">
          {documents.length ? (
            <div className="space-y-2">
              {documents.map((d) => (
                <div key={d.id} className="card flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{d.title}</div>
                    <div className="text-xs text-atlas-muted">{d.doc_type}</div>
                  </div>
                  {d.ingested && <Badge tone="good">indexed</Badge>}
                </div>
              ))}
            </div>
          ) : (
            <Empty>No documents uploaded for this course.</Empty>
          )}
        </Section>
      </div>

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
    </AppShell>
  );
}
