"use client";

import { useRef, useState } from "react";
import { apiGet, apiPost, apiUpload } from "@/lib/api";
import { Modal } from "@/components/ui";
import type { PendingAttachment } from "@/lib/useChat";

/** Paperclip button for the chat composer -- lets the student attach a
 *  document either "for this conversation only" (extracted client-side via
 *  POST /documents/extract-temp, never saved anywhere) or permanently to
 *  the real Documents tab (the normal upload + process flow, just triggered
 *  from the chat composer instead of the Documents page). */
export function ChatAttachButton({
  attachment,
  onAttach,
  onClear,
}: {
  attachment: PendingAttachment | null;
  onAttach: (a: PendingAttachment) => void;
  onClear: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"temp" | "permanent">("temp");
  const [courses, setCourses] = useState<any[] | null>(null);
  const [courseId, setCourseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  function pick() {
    inputRef.current?.click();
  }

  function onFileChosen(e: React.ChangeEvent<HTMLInputElement>) {
    const chosen = e.target.files?.[0] ?? null;
    e.target.value = ""; // lets the same file be re-picked later
    if (!chosen) return;
    setFile(chosen);
    setMode("temp");
    setCourseId("");
    if (!courses) apiGet("/courses").then(setCourses).catch(() => setCourses([]));
  }

  async function confirm() {
    if (!file) return;
    setBusy(true);
    try {
      if (mode === "temp") {
        const form = new FormData();
        form.append("file", file);
        const result = await apiUpload<{ filename: string; text: string; truncated: boolean }>(
          "/documents/extract-temp", form
        );
        onAttach(result);
      } else {
        if (!courseId) return;
        const form = new FormData();
        form.append("file", file);
        form.append("course_id", courseId);
        const uploaded = await apiUpload<{ id: string }>("/documents/upload", form);
        apiPost(`/documents/${uploaded.id}/process`, {}, 120000).catch(() => {});
        setStatus(`Saved "${file.name}" to Documents — indexing now.`);
        setTimeout(() => setStatus(null), 6000);
      }
      setFile(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <input ref={inputRef} type="file" className="hidden" onChange={onFileChosen} />
      <button
        type="button"
        className="btn-ghost !px-2.5"
        title="Attach a document"
        onClick={pick}
      >
        📎
      </button>
      {attachment && (
        <span className="pill text-atlas-accent border-atlas-accent/40 !py-0.5 gap-1">
          📎 {attachment.filename}
          <button
            type="button"
            className="text-atlas-muted hover:text-atlas-text"
            onClick={onClear}
            aria-label="Remove attachment"
          >
            ×
          </button>
        </span>
      )}
      {status && <span className="text-xs text-atlas-good">{status}</span>}

      <Modal
        open={!!file}
        onClose={() => setFile(null)}
        title={file?.name}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setFile(null)}>Cancel</button>
            <button
              className="btn-primary"
              onClick={confirm}
              disabled={busy || (mode === "permanent" && !courseId)}
            >
              {busy ? "Uploading…" : mode === "temp" ? "Attach to this chat" : "Save to Documents"}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="radio" className="mt-1" checked={mode === "temp"}
              onChange={() => setMode("temp")} />
            <span>
              <div className="font-medium">Use for this conversation only</div>
              <div className="text-xs text-atlas-muted">
                Atlas reads it to answer — never saved to your Documents, and gone once this chat's over.
              </div>
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="radio" className="mt-1" checked={mode === "permanent"}
              onChange={() => setMode("permanent")} />
            <span>
              <div className="font-medium">Save to Documents</div>
              <div className="text-xs text-atlas-muted">
                Uploaded like any other document — stays searchable later and shows up on the Documents page.
              </div>
            </span>
          </label>
          {mode === "permanent" && (
            <div>
              <label className="label">Course</label>
              <select className="input" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                <option value="">Select a class…</option>
                {(courses ?? []).map((c: any) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
