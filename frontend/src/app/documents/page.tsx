"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Badge, SkeletonList } from "@/components/ui";
import { apiGet, apiUpload, apiPost, apiDelete, API_BASE } from "@/lib/api";
import { pickFromDrive, driveConfigured } from "@/lib/googleDrive";

const ACCEPT = ".pdf,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.heic,.heif";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [courseId, setCourseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [driveBusy, setDriveBusy] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    const [d, c] = await Promise.all([apiGet("/documents"), apiGet("/courses")]);
    setDocs(d);
    setCourses(c);
  }
  useEffect(() => {
    load();
    // Ping the backend so we can warn clearly if uploads will fail because the
    // API is unreachable (the usual cause of "Couldn't reach the server").
    fetch(`${API_BASE}/health`)
      .then((r) => setBackendDown(!r.ok))
      .catch(() => setBackendDown(true));
  }, []);

  function requireCourse(): boolean {
    if (!courseId) {
      setStatus({ ok: false, text: "Pick a course first — every document must be filed under a class." });
      return false;
    }
    return true;
  }

  function friendlyError(err: any): string {
    const msg = String(err?.message ?? err);
    if (/failed to fetch/i.test(msg)) {
      return "Couldn't reach the server. Check your connection — if this persists the API URL/CORS may need configuring.";
    }
    return msg;
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (!requireCourse()) return;
    setBusy(true);
    setStatus(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("course_id", courseId);
      const res = await apiUpload("/documents/upload", form);
      const title = res.enrichment?.title;
      setStatus({
        ok: true,
        text: `Ingested ${res.chunks} chunks${title ? ` · titled “${title}”` : ""}.`,
      });
      if (fileRef.current) fileRef.current.value = "";
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setBusy(false);
    }
  }

  async function importDrive() {
    if (!requireCourse()) return;
    setDriveBusy(true);
    setStatus(null);
    try {
      const picked = await pickFromDrive();
      if (!picked) return; // canceled
      const res = await apiPost("/documents/import-drive", {
        file_id: picked.id,
        access_token: picked.accessToken,
        course_id: courseId,
        name: picked.name,
        mime_type: picked.mimeType,
      });
      const title = res.enrichment?.title;
      setStatus({
        ok: true,
        text: `Imported “${picked.name}”${title ? ` · titled “${title}”` : ""}.`,
      });
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setDriveBusy(false);
    }
  }

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name ?? "—";
  const driveReady = driveConfigured();

  return (
    <AppShell title="Documents" subtitle="Upload once — searchable forever">
      {backendDown && (
        <div className="card border-atlas-bad/40 text-sm mb-6">
          <div className="font-medium text-atlas-bad">Can't reach the Atlas backend</div>
          <div className="text-atlas-muted mt-1">
            Uploads and Drive imports will fail until the API is reachable. Configured API URL:{" "}
            <code className="text-atlas-text break-all">{API_BASE}</code>. If this is your deployed
            backend, make sure it's running, redeployed with the latest code, allows your site's
            origin (CORS), and permits unauthenticated requests.
          </div>
        </div>
      )}
      <form onSubmit={upload} className="card mb-6">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label">
              Course <span className="text-atlas-bad">*</span>
            </label>
            <select
              className="input !w-48"
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              required
            >
              <option value="">Select a class…</option>
              {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">File</label>
            <input ref={fileRef} type="file" className="text-sm" required accept={ACCEPT} />
          </div>
          <button className="btn-primary" disabled={busy}>{busy ? "Ingesting…" : "Upload"}</button>
          <button
            type="button"
            className="btn-ghost"
            onClick={importDrive}
            disabled={driveBusy || !driveReady}
            title={driveReady
              ? "Pick a file from Google Drive"
              : "Set NEXT_PUBLIC_GOOGLE_CLIENT_ID and NEXT_PUBLIC_GOOGLE_API_KEY to enable"}
          >
            {driveBusy ? "Opening Drive…" : "Import from Drive"}
          </button>
        </div>
        <p className="text-xs text-atlas-muted mt-2">
          Accepts PDF, PowerPoint, notes, and photos (PNG/JPG/HEIC). Images are converted to PDF and
          Atlas generates a title automatically from the contents.
        </p>
        {status && (
          <div className={`text-xs mt-2 ${status.ok ? "text-atlas-good" : "text-atlas-bad"}`}>
            {status.text}
          </div>
        )}
      </form>

      {!docs && <SkeletonList rows={3} />}
      {docs && !docs.length && <Empty>No documents yet. Upload your first file.</Empty>}
      <div className="space-y-2">
        {docs?.map((d) => (
          <div key={d.id} className="card card-hover">
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
