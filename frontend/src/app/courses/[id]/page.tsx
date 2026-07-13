"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Stat, Section, Empty, Loading, Badge, gradeTone } from "@/components/ui";
import { apiGet, apiPatch } from "@/lib/api";

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

const LEVEL_WEIGHT: Record<CourseLevel, number> = {
  regular: 5.0,
  honors: 5.5,
  ap: 6.0,
  dual_enrollment: 6.0,
  ib: 6.0,
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
  const id = params?.id as string;

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

  async function load() {
    try {
      const [c, t, tm, a, g, e, d, an, mi, ss] = await Promise.all([
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
      ]);
      setCourse(c);
      setTeachers(t ?? []);
      setTerms(tm ?? []);
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
  const weight = course.has_hn_prep_lab ? 5.5 : LEVEL_WEIGHT[level];
  const teacherName = teachers.find((t) => t.id === course.teacher_id)?.name;
  const termName = terms.find((t) => t.id === course.term_id)?.name;

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
      <div className="flex flex-wrap gap-2 mb-6">
        {level !== "regular" && <Badge tone="accent">{LEVEL_BADGE[level]}</Badge>}
        {course.has_hn_prep_lab && <Badge tone="warn">HN Prep Lab</Badge>}
        {course.has_ap_prep_lab && <Badge tone="warn">AP Prep Lab</Badge>}
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
          </div>
          <div>
            <label className="label">Term</label>
            <select className="input" value={form.term_id}
              onChange={(e) => setForm({ ...form, term_id: e.target.value })}>
              <option value="">—</option>
              {terms.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
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
        </form>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <Stat
          label="Current grade"
          value={course.current_grade != null ? `${course.current_grade}% ${course.current_letter ?? ""}` : "—"}
          tone={gradeTone(course.current_grade)}
        />
        <Stat label="Weighted scale" value={`${weight.toFixed(1)} max`} />
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
