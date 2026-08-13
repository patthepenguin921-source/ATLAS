"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Empty, Badge, Modal, SkeletonList, ActionMenu } from "@/components/ui";
import { FolderPane } from "@/components/FolderPane";
import { apiGet, apiUpload, apiPost, apiPatch, apiDelete, API_BASE } from "@/lib/api";
import { pickFromDrive, driveConfigured } from "@/lib/googleDrive";
import { groupCourses, currentMember } from "@/lib/courseGroups";

const ACCEPT = ".pdf,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.heic,.heif";

// Sentinel course-select value meaning "General" (not tied to any class) --
// distinct from "" (nothing picked yet), which still blocks upload.
const GENERAL = "__general__";

const IMPORTANCE_LABEL: Record<string, string> = { low: "Low", normal: "Normal", high: "High" };

const DOC_TYPE_LABEL: Record<string, string> = {
  pdf: "PDF", powerpoint: "Slides", notes: "Notes", announcement: "Announcement",
  study_guide: "Study guide", essay: "Essay", practice_problems: "Practice problems",
  rubric: "Rubric", personal_note: "Personal note", email: "Email", image: "Image",
  glance: "At a glance", other: "Other",
};

// A document Atlas can re-fetch directly (no full Schoology sync needed) is
// one linked to a Google Doc/Slides/Sheet — mirrors the backend's
// `google_files.is_google_url` check without needing the parsed file id.
function isResyncable(d: any): boolean {
  const url = d.metadata?.source_url as string | undefined;
  return !!url && (url.includes("docs.google.com") || url.includes("drive.google.com"));
}

// `metadata.review_reason` (set by the Schoology integration) explains *why*
// a document needs a look — falls back to a generic label for the older
// bulk-upload low-confidence-course-match case, which doesn't set a reason.
const REVIEW_REASON_LABEL: Record<string, string> = {
  needs_google_auth: "connect Google Drive",
  unrecognized_link: "unrecognized link",
};

type BulkResult = {
  filename: string;
  id?: string;
  course_id?: string | null;
  needs_review?: boolean;
  course_confidence?: number | null;
  error?: string;
};

// Stable identity for a duplicate pair regardless of which order the
// backend's detector happens to compare the two documents in -- used to key
// both the "which one to keep" choice and the busy-state map below. Mirrors
// the assignments page's identical duplicate-review pattern.
const pairKey = (a: string, b: string) => (a < b ? `${a}:${b}` : `${b}:${a}`);

export default function DocumentsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<any[] | null>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [courseId, setCourseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [driveBusy, setDriveBusy] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const bulkFileRef = useRef<HTMLInputElement>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkPct, setBulkPct] = useState<number | null>(null);
  const [review, setReview] = useState<BulkResult[] | null>(null);
  const [reviewChoice, setReviewChoice] = useState<Record<string, string>>({});
  // Which divider is showing below: "all" is the flat, everything-at-once
  // list (the original behavior); otherwise a class id or "general" shows
  // that divider's own folder tree via FolderPane.
  const [divider, setDivider] = useState<string>("all");
  // Possible-duplicate review (see app.services.document_dedupe) -- fetched
  // separately from `load()` below so a hiccup in this endpoint can never
  // block the document list itself from loading.
  const [duplicates, setDuplicates] = useState<any[]>([]);
  const [keepChoice, setKeepChoice] = useState<Record<string, string>>({});
  const [dupBusy, setDupBusy] = useState<Record<string, boolean>>({});

  async function load() {
    try {
      const [d, c] = await Promise.all([apiGet("/documents"), apiGet("/courses")]);
      setDocs(d);
      setCourses(c);
      setLoadError(null);
      return d;
    } catch (err: any) {
      // Without this, a failed request left `docs` at its initial `null`
      // forever -- the skeleton below just spins with no explanation and no
      // way to retry, which reads as "the documents tab won't load" with no
      // clue why.
      setLoadError(friendlyError(err));
    }
  }
  async function loadDuplicates() {
    try {
      setDuplicates((await apiGet("/documents/possible-duplicates")) ?? []);
    } catch {
      // Non-critical: the review section just stays empty rather than
      // taking the whole page down with it.
      setDuplicates([]);
    }
  }
  useEffect(() => {
    load();
    loadDuplicates();
    // Ping the backend so we can warn clearly if uploads will fail because the
    // API is unreachable (the usual cause of "Couldn't reach the server").
    fetch(`${API_BASE}/health`)
      .then((r) => setBackendDown(!r.ok))
      .catch(() => setBackendDown(true));
  }, []);

  async function mergeDuplicate(candidate: any) {
    const [a, b] = candidate.documents;
    const key = pairKey(a.id, b.id);
    const keepId = keepChoice[key] ?? candidate.suggested_keep_id;
    const discardId = keepId === a.id ? b.id : a.id;
    setDupBusy((s) => ({ ...s, [key]: true }));
    try {
      await apiPost("/documents/merge", { keep_id: keepId, discard_id: discardId });
      setDuplicates((ds) => ds.filter((d) => pairKey(d.documents[0].id, d.documents[1].id) !== key));
      load();
    } finally {
      setDupBusy((s) => ({ ...s, [key]: false }));
    }
  }

  async function dismissDuplicate(candidate: any) {
    const [a, b] = candidate.documents;
    const key = pairKey(a.id, b.id);
    setDupBusy((s) => ({ ...s, [key]: true }));
    try {
      await apiPost("/documents/dismiss-duplicate", { document_id_a: a.id, document_id_b: b.id });
      setDuplicates((ds) => ds.filter((d) => pairKey(d.documents[0].id, d.documents[1].id) !== key));
    } finally {
      setDupBusy((s) => ({ ...s, [key]: false }));
    }
  }

  // Chunk/embed + AI enrichment happen in a separate request that's kicked
  // off right after upload (see `triggerProcessing` below), so a freshly
  // uploaded document sits at `ingested: false` ("pending") for a bit. Poll
  // gently while anything is still pending so the list flips to "indexed"
  // on its own instead of requiring a manual refresh.
  useEffect(() => {
    const stillPending = docs?.some((d) => !d.ingested && !d.ingest_error);
    if (!stillPending) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [docs]);

  // Fire-and-forget: ask the backend to index a document now. Called right
  // after upload/bulk-upload/Drive-import, and by the "Index now" button for
  // anything that missed that call or previously failed. Errors are silent
  // here — the safety-net cron and the button both remain as fallbacks.
  function triggerProcessing(id: string) {
    apiPost(`/documents/${id}/process`, {}, 120000)
      .catch(() => {})
      .finally(load);
  }

  function requireCourse(): boolean {
    if (!courseId) {
      setStatus({ ok: false, text: "Pick a class first, or choose General if it doesn't belong to one." });
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
    setUploadPct(0);
    setStatus(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (courseId === GENERAL) form.append("general", "true");
      else form.append("course_id", courseId);
      const result = await apiUpload("/documents/upload", form, setUploadPct);
      if (result?.id) triggerProcessing(result.id);
      setStatus({
        ok: true,
        text: "Uploaded — processing in the background. It'll show as “indexed” below shortly.",
      });
      if (fileRef.current) fileRef.current.value = "";
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }

  async function importDrive() {
    if (!requireCourse()) return;
    if (courseId === GENERAL) {
      setStatus({ ok: false, text: "Drive import needs a specific class — General isn't supported for it yet." });
      return;
    }
    setDriveBusy(true);
    setStatus(null);
    try {
      const picked = await pickFromDrive();
      if (!picked) return; // canceled
      const result = await apiPost("/documents/import-drive", {
        file_id: picked.id,
        access_token: picked.accessToken,
        course_id: courseId,
        name: picked.name,
        mime_type: picked.mimeType,
      });
      if (result?.id) triggerProcessing(result.id);
      setStatus({
        ok: true,
        text: `Imported “${picked.name}” — processing in the background.`,
      });
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setDriveBusy(false);
    }
  }

  async function bulkUpload(e: React.FormEvent) {
    e.preventDefault();
    const files = bulkFileRef.current?.files;
    if (!files || !files.length) return;
    setBulkBusy(true);
    setBulkPct(0);
    setStatus(null);
    try {
      const form = new FormData();
      Array.from(files).forEach((f) => form.append("files", f));
      const res = await apiUpload("/documents/bulk-upload", form, setBulkPct);
      const results: BulkResult[] = res.results ?? [];
      results.forEach((r) => {
        if (r.id) triggerProcessing(r.id);
      });
      const flagged = results.filter((r) => r.needs_review && r.id);
      const failed = results.filter((r) => r.error);
      const filed = results.length - flagged.length - failed.length;
      setStatus({
        ok: failed.length === 0,
        text: `${filed} filed automatically${flagged.length ? `, ${flagged.length} need a quick check` : ""}${
          failed.length ? `, ${failed.length} failed` : ""
        }.`,
      });
      if (bulkFileRef.current) bulkFileRef.current.value = "";
      if (flagged.length) {
        setReview(flagged);
        setReviewChoice(Object.fromEntries(flagged.map((r) => [r.id!, r.course_id ?? ""])));
      }
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setBulkBusy(false);
      setBulkPct(null);
    }
  }

  async function confirmReview(id: string) {
    const chosen = reviewChoice[id];
    if (!chosen) return;
    await apiPatch(`/documents/${id}`, { course_id: chosen });
    setReview((r) => (r ? r.filter((x) => x.id !== id) : r));
    load();
  }

  // Archivist suggests an initial importance level when a document is
  // ingested; this is the "adjustable after the fact" override — picking a
  // value here marks it manual so a later re-enrichment never reverts it
  // (see the backend's `importance_source` handling).
  async function setImportance(id: string, importance: string) {
    setDocs((prev) => prev?.map((d) => (d.id === id ? { ...d, importance } : d)) ?? prev);
    try {
      await apiPatch(`/documents/${id}`, { importance });
    } catch {
      load(); // revert the optimistic update on failure
    }
  }

  // Same "adjustable after the fact" idea as importance above — a manual
  // re-tag is marked `doc_type_source: manual` server-side so neither the
  // next re-enrichment nor a glance re-sync ever reverts it.
  async function setDocType(id: string, doc_type: string) {
    setDocs((prev) => prev?.map((d) => (d.id === id ? { ...d, doc_type } : d)) ?? prev);
    try {
      await apiPatch(`/documents/${id}`, { doc_type });
    } catch {
      load(); // revert the optimistic update on failure
    }
  }

  const [resyncingId, setResyncingId] = useState<string | null>(null);
  async function resyncDocument(id: string) {
    setResyncingId(id);
    setStatus(null);
    try {
      const result = await apiPost(`/documents/${id}/resync`, {}, 60000);
      setStatus({
        ok: true,
        text: result?.changed
          ? "Re-fetched the latest version — reprocessing now."
          : "Already up to date — nothing changed since the last sync.",
      });
      load();
    } catch (err: any) {
      setStatus({ ok: false, text: friendlyError(err) });
    } finally {
      setResyncingId(null);
    }
  }

  const courseName = (id: string | null) => (id ? courses.find((c) => c.id === id)?.name ?? "—" : "General");
  // Completed classes (Schoology flips `is_active` false once a grading
  // period ends) shouldn't be offered as a destination for new files —
  // existing documents already filed under one still show up fine via
  // `courseName` above, which isn't filtered.
  const activeCourses = courses.filter((c) => c.is_active !== false);
  // A class split into linked semester rows (see lib/courseGroups) is still
  // one real class -- group them so it shows up as a single upload option /
  // divider pill instead of duplicated once per semester row.
  const courseGroupList = groupCourses(activeCourses);
  const dividerGroup = courseGroupList.find((g) => g.key === divider);
  const driveReady = driveConfigured();

  async function renameDocument(id: string, currentTitle: string) {
    const title = window.prompt("Rename document:", currentTitle)?.trim();
    if (!title || title === currentTitle) return;
    setDocs((prev) => prev?.map((d) => (d.id === id ? { ...d, title } : d)) ?? prev);
    try {
      await apiPatch(`/documents/${id}`, { title });
    } catch {
      load(); // revert the optimistic update on failure
    }
  }

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
              {courseGroupList.map((g) => (
                <option key={g.key} value={currentMember(g).id}>{g.primary.name}</option>
              ))}
              <option value={GENERAL}>General (not a specific class)</option>
            </select>
          </div>
          <div>
            <label className="label">File</label>
            <input ref={fileRef} type="file" className="text-sm" required accept={ACCEPT} />
          </div>
          <button className="btn-primary" disabled={busy}>
            {busy
              ? uploadPct != null && uploadPct < 100
                ? `Uploading… ${uploadPct}%`
                : "Processing…"
              : "Upload"}
          </button>
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

      <form onSubmit={bulkUpload} className="card mb-6">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label">Dump multiple files</label>
            <input ref={bulkFileRef} type="file" className="text-sm" multiple required accept={ACCEPT} />
          </div>
          <button className="btn-ghost" disabled={bulkBusy}>
            {bulkBusy
              ? bulkPct != null && bulkPct < 100
                ? `Uploading… ${bulkPct}%`
                : "Filing…"
              : "Upload & auto-file"}
          </button>
        </div>
        <p className="text-xs text-atlas-muted mt-2">
          No class needed — Atlas reads each file and matches it to one of your existing classes.
          Anything it's not confident about gets flagged for a quick check.
        </p>
      </form>

      {duplicates.length > 0 && (
        <div className="card border-atlas-warn/40 mb-6 space-y-3">
          <div>
            <div className="text-sm font-medium text-atlas-warn">
              Possible duplicate documents ({duplicates.length})
            </div>
            <div className="text-xs text-atlas-muted mt-0.5">
              These look like the same file added twice — e.g. once from Drive, once uploaded by hand.
              Pick which one to keep (its content, chunks, and flashcards absorb the other's), or tell us
              they're actually different files.
            </div>
          </div>
          {duplicates.map((d) => {
            const [a, b] = d.documents;
            const key = pairKey(a.id, b.id);
            const keep = keepChoice[key] ?? d.suggested_keep_id;
            const busy = !!dupBusy[key];
            const keepTitle = (keep === a.id ? a : b).title;
            return (
              <div key={key} className="border border-atlas-border rounded-lg p-3 space-y-2">
                <div className="grid sm:grid-cols-2 gap-2">
                  {[a, b].map((x) => (
                    <label
                      key={x.id}
                      className={`flex items-start gap-2 rounded-md border p-2 cursor-pointer text-sm ${
                        keep === x.id ? "border-atlas-accent" : "border-atlas-border"
                      }`}
                    >
                      <input
                        type="radio"
                        className="mt-1"
                        checked={keep === x.id}
                        onChange={() => setKeepChoice((k) => ({ ...k, [key]: x.id }))}
                      />
                      <div className="min-w-0">
                        <div className="font-medium truncate">{x.title}</div>
                        <div className="text-xs text-atlas-muted">
                          {courseName(x.course_id)} · {DOC_TYPE_LABEL[x.doc_type] ?? x.doc_type ?? "—"}
                          {x.ingested ? " · indexed" : " · processing…"}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn-primary text-xs py-1.5"
                    disabled={busy}
                    onClick={() => mergeDuplicate(d)}
                  >
                    {busy ? "Merging…" : `Keep "${keepTitle}", merge the other in`}
                  </button>
                  <button className="btn-ghost text-xs py-1.5" disabled={busy} onClick={() => dismissDuplicate(d)}>
                    Not the same document
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!docs && !loadError && <SkeletonList rows={3} />}
      {loadError && !docs && (
        <div className="card border-atlas-bad/40 text-sm mb-6">
          <div className="font-medium text-atlas-bad">Couldn't load your documents</div>
          <div className="text-atlas-muted mt-1">{loadError}</div>
          <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
        </div>
      )}
      {docs && !docs.length && <Empty>No documents yet. Upload your first file.</Empty>}

      {docs && docs.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          <button
            className={`pill ${divider === "all" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
            onClick={() => setDivider("all")}
          >
            All
          </button>
          {courseGroupList.map((g) => (
            <button
              key={g.key}
              className={`pill ${divider === g.key ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
              onClick={() => setDivider(g.key)}
            >
              {g.primary.name}
            </button>
          ))}
          <button
            className={`pill ${divider === "general" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
            onClick={() => setDivider("general")}
          >
            General
          </button>
        </div>
      )}

      {divider !== "all" ? (
        <FolderPane
          courseId={
            divider === "general"
              ? null
              : dividerGroup?.members.map((m) => m.id).join(",") ?? divider
          }
          uploadCourseId={
            divider === "general" ? null : dividerGroup ? currentMember(dividerGroup).id : divider
          }
        />
      ) : (
      <div className="space-y-2">
        {docs?.map((d) => (
          <div
            key={d.id}
            className="card card-hover cursor-pointer"
            onClick={() => router.push(`/documents/${d.id}`)}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="font-medium">{d.title}</div>
                <div className="text-xs text-atlas-muted mt-0.5">
                  {courseName(d.course_id)} · {DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}
                </div>
                {d.summary && <p className="text-sm text-atlas-muted mt-2">{d.summary}</p>}
                {d.keywords?.length ? (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {d.keywords.slice(0, 8).map((k: string) => <Badge key={k}>{k}</Badge>)}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-col items-end gap-2 shrink-0">
                <div className="flex items-center gap-2">
                  <Badge tone={d.ingested ? "good" : d.ingest_error ? "bad" : "warn"}>
                    {d.ingested ? "indexed" : d.ingest_error ? "failed" : "processing…"}
                  </Badge>
                  <div onClick={(e) => e.stopPropagation()}>
                    <ActionMenu
                      items={[
                        ...(!d.ingested
                          ? [{ label: "Index now", onClick: () => triggerProcessing(d.id) }]
                          : []),
                        ...(isResyncable(d)
                          ? [{
                              label: resyncingId === d.id ? "Re-fetching…" : "Re-fetch from source",
                              disabled: resyncingId === d.id,
                              onClick: () => resyncDocument(d.id),
                            }]
                          : []),
                        { label: "Rename", onClick: () => renameDocument(d.id, d.title) },
                        {
                          label: "Delete",
                          danger: true,
                          onClick: async () => {
                            await apiDelete(`/documents/${d.id}`);
                            load();
                          },
                        },
                      ]}
                    />
                  </div>
                </div>
                {d.needs_review && (
                  <Badge tone="warn">
                    {REVIEW_REASON_LABEL[d.metadata?.review_reason] ?? "check class"}
                  </Badge>
                )}
                {d.importance === "high" && <Badge tone="accent">important</Badge>}
                {d.importance === "low" && <Badge>low priority</Badge>}
                <select
                  className="input !w-32 text-xs py-1"
                  value={d.doc_type ?? "other"}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setDocType(d.id, e.target.value)}
                  title="Document type"
                >
                  {Object.entries(DOC_TYPE_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <select
                  className="input !w-28 text-xs py-1"
                  value={d.importance ?? "normal"}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setImportance(d.id, e.target.value)}
                  title="Importance"
                >
                  {Object.entries(IMPORTANCE_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        ))}
      </div>
      )}

      <Modal
        open={!!review?.length}
        onClose={() => setReview(null)}
        title="Double-check these classes"
      >
        <div className="space-y-3">
          <p className="text-sm text-atlas-muted">
            Atlas wasn't confident which class these belong to. Pick the right one for each.
          </p>
          {review?.map((r) => (
            <div key={r.id} className="flex items-center gap-2">
              <span className="text-sm truncate flex-1" title={r.filename}>{r.filename}</span>
              <select
                className="input !w-40"
                value={reviewChoice[r.id!] ?? ""}
                onChange={(e) => setReviewChoice((c) => ({ ...c, [r.id!]: e.target.value }))}
              >
                <option value="">Select a class…</option>
                {courseGroupList.map((g) => (
                  <option key={g.key} value={currentMember(g).id}>{g.primary.name}</option>
                ))}
              </select>
              <button
                className="btn-ghost text-xs"
                disabled={!reviewChoice[r.id!]}
                onClick={() => confirmReview(r.id!)}
              >
                Confirm
              </button>
            </div>
          ))}
        </div>
      </Modal>
    </AppShell>
  );
}
