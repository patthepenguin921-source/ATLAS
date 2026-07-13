"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge } from "@/components/ui";
import { apiGet, apiUpload, apiDelete } from "@/lib/api";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [courseId, setCourseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    const [d, c] = await Promise.all([apiGet("/documents"), apiGet("/courses")]);
    setDocs(d);
    setCourses(c);
  }
  useEffect(() => {
    load();
  }, []);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setStatus(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (courseId) form.append("course_id", courseId);
      const res = await apiUpload("/documents/upload", form);
      setStatus(`Ingested ${res.chunks} chunks${res.enrichment?.summary ? " · summarized" : ""}.`);
      if (fileRef.current) fileRef.current.value = "";
      load();
    } catch (err: any) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  }

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "—";

  return (
    <AppShell title="Documents" subtitle="Upload once — searchable forever">
      <form onSubmit={upload} className="card mb-6 flex flex-wrap items-end gap-3">
        <div>
          <label className="label">File (PDF, PPTX, notes)</label>
          <input ref={fileRef} type="file" className="text-sm" required
            accept=".pdf,.pptx,.ppt,.txt,.md" />
        </div>
        <div>
          <label className="label">Course (optional)</label>
          <select className="input !w-48" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
            <option value="">—</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <button className="btn-primary" disabled={busy}>{busy ? "Ingesting…" : "Upload"}</button>
        {status && <span className="text-xs text-atlas-muted">{status}</span>}
      </form>

      {!docs && <Loading />}
      {docs && !docs.length && <Empty>No documents yet. Upload your first file.</Empty>}
      <div className="space-y-2">
        {docs?.map((d) => (
          <div key={d.id} className="card">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="font-medium">{d.title}</div>
                <div className="text-xs text-atlas-muted mt-0.5">
                  {courseName(d.course_id)} · {d.doc_type}
                </div>
                {d.summary && <p className="text-sm text-atlas-muted mt-2">{d.summary}</p>}
                {d.keywords?.length ? (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {d.keywords.slice(0, 8).map((k: string) => <Badge key={k}>{k}</Badge>)}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-col items-end gap-2 shrink-0">
                <Badge tone={d.ingested ? "good" : "warn"}>{d.ingested ? "indexed" : "pending"}</Badge>
                <button className="text-xs text-atlas-bad hover:underline"
                  onClick={async () => { await apiDelete(`/documents/${d.id}`); load(); }}>
                  delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </AppShell>
  );
}
