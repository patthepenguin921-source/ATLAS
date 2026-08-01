"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Skeleton, Modal, Badge } from "@/components/ui";
import { Icon } from "@/components/Icon";
import { apiGet } from "@/lib/api";
import { courseColor } from "@/lib/courseColor";

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** Local YYYY-MM-DD for a grid day -- built from local Y/M/D components, not
 *  `toISOString()` (which is UTC-based and would shift the date for anyone
 *  west of UTC, the same bug `formatCalendarDate` exists to avoid). */
function localKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The calendar day a stored event/assignment belongs on. These are
 *  overwhelmingly date-only values stored at UTC midnight (see
 *  `frontend/src/lib/date.ts`), so the UTC date portion of the ISO string is
 *  the actual intended day -- slicing it directly sidesteps any local-
 *  timezone reinterpretation entirely, same fix as `formatCalendarDate`. */
function itemKey(iso: string): string {
  return iso.slice(0, 10);
}

type CalItem = {
  id: string;
  kind: "event" | "assignment";
  title: string;
  course_id: string | null;
  dateKey: string;
  detail?: string;
  href?: string;
};

export default function CalendarPage() {
  const router = useRouter();
  const today = new Date();
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [courses, setCourses] = useState<any[]>([]);
  const [data, setData] = useState<{ events: any[]; assignments: any[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  useEffect(() => {
    apiGet("/courses").then(setCourses).catch(() => setCourses([]));
  }, []);

  // Full weeks covering the month, so the grid never has a ragged first/last row.
  const gridStart = useMemo(() => {
    const d = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    d.setDate(d.getDate() - d.getDay());
    return d;
  }, [cursor]);
  const gridDays = useMemo(() => {
    const days: Date[] = [];
    const d = new Date(gridStart);
    while (days.length < 42) {
      days.push(new Date(d));
      d.setDate(d.getDate() + 1);
    }
    return days;
  }, [gridStart]);
  const gridEnd = gridDays[gridDays.length - 1];

  useEffect(() => {
    setLoading(true);
    const start = localKey(gridStart);
    // Exclusive end — the day *after* the last grid day.
    const endExclusive = new Date(gridEnd);
    endExclusive.setDate(endExclusive.getDate() + 1);
    const end = localKey(endExclusive);
    apiGet(`/dashboard/calendar-range?start=${start}&end=${end}`)
      .then(setData)
      .catch(() => setData({ events: [], assignments: [] }))
      .finally(() => setLoading(false));
  }, [gridStart, gridEnd]);

  const courseName = (id: string | null) => courses.find((c) => c.id === id)?.name ?? "Other";

  const itemsByDay = useMemo(() => {
    const map = new Map<string, CalItem[]>();
    for (const e of data?.events ?? []) {
      if (!e.starts_at) continue;
      const key = itemKey(e.starts_at);
      const list = map.get(key) ?? [];
      list.push({
        id: `event-${e.id}`, kind: "event", title: e.title, course_id: e.course_id,
        dateKey: key, detail: e.kind !== "class" ? e.kind : undefined,
      });
      map.set(key, list);
    }
    for (const a of data?.assignments ?? []) {
      if (!a.due_date) continue;
      const key = itemKey(a.due_date);
      const list = map.get(key) ?? [];
      list.push({
        id: `assignment-${a.id}`, kind: "assignment", title: a.title, course_id: a.course_id,
        dateKey: key, detail: a.category,
      });
      map.set(key, list);
    }
    return map;
  }, [data]);

  const monthLabel = cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const todayKey = localKey(today);
  const selectedItems = selectedDay ? (itemsByDay.get(selectedDay) ?? []) : [];

  return (
    <AppShell
      title="Calendar"
      subtitle="Every class day, event, and due date across all your courses"
      actions={
        <div className="flex items-center gap-2">
          <button className="btn-ghost !px-3" onClick={() => setCursor(new Date(today.getFullYear(), today.getMonth(), 1))}>
            Today
          </button>
          <button
            className="btn-ghost !px-3"
            onClick={() => setCursor((c) => new Date(c.getFullYear(), c.getMonth() - 1, 1))}
            aria-label="Previous month"
          >
            <Icon name="chevronLeft" className="w-4 h-4" />
          </button>
          <div className="text-sm font-medium w-36 text-center">{monthLabel}</div>
          <button
            className="btn-ghost !px-3"
            onClick={() => setCursor((c) => new Date(c.getFullYear(), c.getMonth() + 1, 1))}
            aria-label="Next month"
          >
            <Icon name="chevronRight" className="w-4 h-4" />
          </button>
        </div>
      }
    >
      {loading && !data ? (
        <div className="card !p-0 overflow-hidden">
          <div className="grid grid-cols-7 border-b border-atlas-border">
            {WEEKDAY_LABELS.map((w) => (
              <div key={w} className="px-2 py-2 text-xs font-medium text-atlas-muted text-center">{w}</div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {Array.from({ length: 42 }).map((_, i) => (
              <div key={i} className="min-h-24 border-b border-r border-atlas-border p-1.5">
                <Skeleton className="h-3 w-5" />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card !p-0 overflow-hidden">
          <div className="grid grid-cols-7 border-b border-atlas-border">
            {WEEKDAY_LABELS.map((w) => (
              <div key={w} className="px-2 py-2 text-xs font-medium text-atlas-muted text-center">
                {w}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {gridDays.map((d) => {
              const key = localKey(d);
              const inMonth = d.getMonth() === cursor.getMonth();
              const isToday = key === todayKey;
              const items = itemsByDay.get(key) ?? [];
              const visible = items.slice(0, 3);
              const overflow = items.length - visible.length;
              const hasItems = items.length > 0;
              const cellClassName = `min-h-24 border-b border-r border-atlas-border p-1.5 text-left align-top transition-colors ${
                inMonth ? "bg-transparent" : "bg-atlas-panel2/40"
              } ${hasItems ? "hover:bg-atlas-panel2 cursor-pointer" : ""}`;
              const cellContent = (
                <>
                  <div
                    className={`text-xs mb-1 inline-flex items-center justify-center w-5 h-5 rounded-full ${
                      isToday ? "bg-atlas-accent text-white" : inMonth ? "text-atlas-text" : "text-atlas-muted"
                    }`}
                  >
                    {d.getDate()}
                  </div>
                  <div className="space-y-0.5">
                    {visible.map((item) => (
                      <div
                        key={item.id}
                        className="text-[10px] leading-tight truncate rounded px-1 py-0.5 flex items-center gap-0.5"
                        style={{
                          background: `${courseColor(item.course_id ?? "other", courses.find((c) => c.id === item.course_id)?.color)}22`,
                          color: courseColor(item.course_id ?? "other", courses.find((c) => c.id === item.course_id)?.color),
                        }}
                        title={item.title}
                      >
                        {item.kind === "assignment" && <Icon name="chevronRight" className="w-2.5 h-2.5 shrink-0" />}
                        <span className="truncate">{item.title}</span>
                      </div>
                    ))}
                    {overflow > 0 && (
                      <div className="text-[10px] text-atlas-muted px-1">+{overflow} more</div>
                    )}
                  </div>
                </>
              );
              return hasItems ? (
                <button key={key} onClick={() => setSelectedDay(key)} className={cellClassName}>
                  {cellContent}
                </button>
              ) : (
                <div key={key} className={cellClassName}>
                  {cellContent}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <Modal
        open={!!selectedDay}
        onClose={() => setSelectedDay(null)}
        title={selectedDay ? new Date(selectedDay + "T00:00:00").toLocaleDateString(undefined, {
          weekday: "long", month: "long", day: "numeric",
        }) : ""}
      >
        <div className="space-y-2">
          {selectedItems.map((item) => (
            <button
              key={item.id}
              className="w-full text-left card card-hover flex items-center justify-between gap-3"
              onClick={() => {
                setSelectedDay(null);
                if (item.course_id) router.push(`/courses/${item.course_id}`);
              }}
            >
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{item.title}</div>
                <div className="text-xs text-atlas-muted">{courseName(item.course_id)}</div>
              </div>
              <Badge tone={item.kind === "assignment" ? "accent" : "default"}>
                {item.kind === "assignment" ? (item.detail ?? "assignment") : (item.detail ?? "class")}
              </Badge>
            </button>
          ))}
        </div>
      </Modal>
    </AppShell>
  );
}
