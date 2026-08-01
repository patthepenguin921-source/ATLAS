"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Empty, Badge, ActionMenu, Loading, Modal } from "@/components/ui";
import { Icon } from "@/components/Icon";
import { apiGet, apiPost, apiPatch, apiDelete, apiUpload } from "@/lib/api";

type FolderRow = {
  id: string;
  name: string;
  parent_folder_id: string | null;
  course_id: string | null;
  is_general?: boolean;
};

type DocRow = {
  id: string;
  title: string;
  doc_type: string;
  summary?: string;
  folder_id: string | null;
  folder_source?: string | null;
  ingested: boolean;
  ingest_error?: string | null;
  importance?: string;
};

const ACCEPT = ".pdf,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.heic,.heif";

const DOC_TYPE_LABEL: Record<string, string> = {
  pdf: "PDF", powerpoint: "Slides", notes: "Notes", announcement: "Announcement",
  study_guide: "Study guide", essay: "Essay", practice_problems: "Practice problems",
  rubric: "Rubric", personal_note: "Personal note", email: "Email", image: "Image",
  glance: "At a glance", other: "Other",
};

function groupByParent(folders: FolderRow[]): Map<string | null, FolderRow[]> {
  const byParent = new Map<string | null, FolderRow[]>();
  for (const f of folders) {
    const key = f.parent_folder_id ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(f);
  }
  return byParent;
}

function FolderNode({
  folder, byParent, depth, selected, onSelect, onAddChild, onRename, onDelete,
}: {
  folder: FolderRow;
  byParent: Map<string | null, FolderRow[]>;
  depth: number;
  selected: string;
  onSelect: (id: string) => void;
  onAddChild: (parentId: string) => void;
  onRename: (folder: FolderRow) => void;
  onDelete: (folder: FolderRow) => void;
}) {
  const children = byParent.get(folder.id) ?? [];
  return (
    <div>
      <div
        className={`flex items-center justify-between gap-1 rounded-lg px-2 py-1 cursor-pointer text-sm ${
          selected === folder.id ? "bg-atlas-accent/15 text-atlas-accent" : "hover:bg-atlas-panel2"
        }`}
        style={{ marginLeft: depth * 14 }}
        onClick={() => onSelect(folder.id)}
      >
        <span className="truncate flex items-center gap-1.5">
          <Icon name="folder" className="w-3.5 h-3.5 text-atlas-muted shrink-0" />
          {folder.name}
        </span>
        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          <button
            className="text-atlas-muted hover:text-atlas-text text-xs px-1"
            title="New subfolder"
            onClick={() => onAddChild(folder.id)}
          >
            +
          </button>
          <ActionMenu
            items={[
              { label: "Rename", onClick: () => onRename(folder) },
              { label: "Delete", danger: true, onClick: () => onDelete(folder) },
            ]}
          />
        </div>
      </div>
      {children.map((c) => (
        <FolderNode
          key={c.id} folder={c} byParent={byParent} depth={depth + 1}
          selected={selected} onSelect={onSelect} onAddChild={onAddChild}
          onRename={onRename} onDelete={onDelete}
        />
      ))}
    </div>
  );
}

/** Per-class (or "General") folder tree + the documents filed in it —
 *  shared by the Documents page's class dividers and the course detail
 *  page's own "Files" section. `courseId` omitted/null means the
 *  "General" divider, for documents that don't belong to any class. */
export function FolderPane({
  courseId, onDocClick,
}: {
  courseId?: string | null;
  onDocClick?: (id: string) => void;
}) {
  const router = useRouter();
  const scopeQuery = courseId ? `course_id=${courseId}` : "general=true";

  const [folders, setFolders] = useState<FolderRow[] | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [filter, setFilter] = useState<string>("all"); // "all" | "unfiled" | a folder id
  const [newFolderName, setNewFolderName] = useState("");
  const [addingUnder, setAddingUnder] = useState<string | null>(null);
  const [subfolderName, setSubfolderName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [renameTarget, setRenameTarget] = useState<FolderRow | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<FolderRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadFolders() {
    const f = await apiGet(`/folders?${scopeQuery}`);
    setFolders(f ?? []);
  }

  async function loadDocs(activeFilter = filter) {
    let q = scopeQuery;
    if (activeFilter === "unfiled") q += "&unfiled=true";
    else if (activeFilter !== "all") q += `&folder_id=${activeFilter}`;
    const d = await apiGet(`/documents?${q}`);
    setDocs(d ?? []);
  }

  useEffect(() => {
    setFilter("all");
    loadFolders();
    loadDocs("all");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId]);

  useEffect(() => {
    loadDocs(filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  // Poll gently while anything's still processing — same pattern as the
  // documents page.
  useEffect(() => {
    const pending = docs?.some((d) => !d.ingested && !d.ingest_error);
    if (!pending) return;
    const t = setInterval(() => loadDocs(), 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs]);

  async function createTopFolder() {
    const name = newFolderName.trim();
    if (!name) return;
    await apiPost("/folders", courseId ? { name, course_id: courseId } : { name });
    setNewFolderName("");
    loadFolders();
  }

  async function createSubfolder(parentId: string) {
    const name = subfolderName.trim();
    if (!name) return;
    await apiPost("/folders", { name, parent_folder_id: parentId });
    setSubfolderName("");
    setAddingUnder(null);
    loadFolders();
  }

  function openRename(folder: FolderRow) {
    setRenameTarget(folder);
    setRenameValue(folder.name);
  }

  async function submitRename() {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name || name === renameTarget.name) {
      setRenameTarget(null);
      return;
    }
    setRenaming(true);
    try {
      await apiPatch(`/folders/${renameTarget.id}`, { name });
      setRenameTarget(null);
      loadFolders();
    } finally {
      setRenaming(false);
    }
  }

  async function confirmDeleteFolder() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiDelete(`/folders/${deleteTarget.id}`);
      if (filter === deleteTarget.id) setFilter("all");
      loadFolders();
      loadDocs(filter === deleteTarget.id ? "all" : filter);
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  async function moveDocument(docId: string, folderId: string) {
    setDocs((prev) => prev?.map((d) => (d.id === docId ? { ...d, folder_id: folderId || null } : d)) ?? prev);
    try {
      await apiPatch(`/documents/${docId}`, { folder_id: folderId || null });
    } finally {
      loadDocs();
    }
  }

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setStatus(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (courseId) form.append("course_id", courseId);
      else form.append("general", "true");
      if (filter !== "all" && filter !== "unfiled") form.append("folder_id", filter);
      const result = await apiUpload<any>("/documents/upload", form);
      if (result?.id) {
        apiPost(`/documents/${result.id}/process`, {}, 120000).catch(() => {}).finally(() => loadDocs());
      }
      setStatus({ ok: true, text: `Uploaded "${file.name}" — processing…` });
      loadDocs();
    } catch (err: any) {
      setStatus({ ok: false, text: err.message });
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  if (!folders || !docs) return <Loading />;

  const byParent = groupByParent(folders);
  const topLevel = byParent.get(null) ?? [];

  const flatOpts: { id: string; label: string }[] = [];
  (function walk(parentId: string | null, depth: number) {
    for (const f of byParent.get(parentId) ?? []) {
      flatOpts.push({ id: f.id, label: `${"— ".repeat(depth)}${f.name}` });
      walk(f.id, depth + 1);
    }
  })(null, 0);

  return (
    <div className="grid md:grid-cols-[15rem_1fr] gap-4">
      <div className="space-y-1">
        <div className="text-xs uppercase text-atlas-muted tracking-wide mb-1">Folders</div>
        <div
          className={`rounded-lg px-2 py-1 cursor-pointer text-sm ${
            filter === "all" ? "bg-atlas-accent/15 text-atlas-accent" : "hover:bg-atlas-panel2"
          }`}
          onClick={() => setFilter("all")}
        >
          All documents
        </div>
        <div
          className={`rounded-lg px-2 py-1 cursor-pointer text-sm ${
            filter === "unfiled" ? "bg-atlas-accent/15 text-atlas-accent" : "hover:bg-atlas-panel2"
          }`}
          onClick={() => setFilter("unfiled")}
        >
          Top level (unfiled)
        </div>
        {topLevel.map((f) => (
          <FolderNode
            key={f.id} folder={f} byParent={byParent} depth={0}
            selected={filter} onSelect={setFilter}
            onAddChild={(id) => setAddingUnder(id)}
            onRename={openRename} onDelete={setDeleteTarget}
          />
        ))}
        {addingUnder && (
          <div className="flex gap-1 mt-1" style={{ marginLeft: 14 }}>
            <input
              className="input !py-1 text-xs" autoFocus placeholder="Subfolder name…"
              value={subfolderName}
              onChange={(e) => setSubfolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") createSubfolder(addingUnder);
                if (e.key === "Escape") setAddingUnder(null);
              }}
            />
            <button className="btn-ghost text-xs py-1" onClick={() => createSubfolder(addingUnder)}>Add</button>
          </div>
        )}
        <div className="flex gap-1 mt-2">
          <input
            className="input !py-1 text-xs" placeholder="New folder…"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createTopFolder()}
          />
          <button className="btn-ghost text-xs py-1" onClick={createTopFolder} disabled={!newFolderName.trim()}>+</button>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between gap-3 mb-3">
          <label className="btn-ghost text-xs cursor-pointer">
            {uploading ? "Uploading…" : "+ Upload here"}
            <input type="file" className="hidden" accept={ACCEPT} onChange={upload} disabled={uploading} />
          </label>
          {status && (
            <span className={`text-xs truncate ${status.ok ? "text-atlas-good" : "text-atlas-bad"}`}>
              {status.text}
            </span>
          )}
        </div>
        {docs.length ? (
          <div className="space-y-2">
            {docs.map((d) => (
              <div key={d.id} className="card flex items-start justify-between gap-3">
                <div
                  className="min-w-0 cursor-pointer"
                  onClick={() => (onDocClick ? onDocClick(d.id) : router.push(`/documents/${d.id}`))}
                >
                  <div className="text-sm font-medium truncate">{d.title}</div>
                  <div className="text-xs text-atlas-muted">{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}</div>
                  {d.summary && <p className="text-xs text-atlas-muted mt-1 line-clamp-2">{d.summary}</p>}
                </div>
                <div className="flex flex-col items-end gap-1.5 shrink-0">
                  <Badge tone={d.ingested ? "good" : d.ingest_error ? "bad" : "warn"}>
                    {d.ingested ? "indexed" : d.ingest_error ? "failed" : "processing…"}
                  </Badge>
                  <select
                    className="input !w-36 text-xs py-1"
                    value={d.folder_id ?? ""}
                    onChange={(e) => moveDocument(d.id, e.target.value)}
                    title="Move to folder"
                  >
                    <option value="">Top level</option>
                    {flatOpts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No documents here yet.</Empty>
        )}
      </div>

      <Modal
        open={!!renameTarget}
        onClose={() => setRenameTarget(null)}
        title="Rename folder"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setRenameTarget(null)}>Cancel</button>
            <button
              className="btn-primary"
              disabled={renaming || !renameValue.trim()}
              onClick={submitRename}
            >
              {renaming ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        <input
          className="input"
          autoFocus
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitRename()}
        />
      </Modal>

      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title={`Delete "${deleteTarget?.name}"?`}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setDeleteTarget(null)}>Cancel</button>
            <button
              className="btn-primary !bg-atlas-bad hover:!brightness-110"
              disabled={deleting}
              onClick={confirmDeleteFolder}
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        <p className="text-sm text-atlas-muted">
          Documents inside move back to the top level — they aren&apos;t deleted.
        </p>
      </Modal>
    </div>
  );
}
