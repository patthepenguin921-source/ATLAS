"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { AgentPicker } from "@/components/AgentPicker";
import { ChatAttachButton } from "@/components/ChatAttachButton";
import { ChatMessageActions } from "@/components/ChatMessageActions";
import { ChatMessageContent } from "@/components/ChatMessageContent";
import { FolderPane } from "@/components/FolderPane";
import { ThinkingDots } from "@/components/ThinkingDots";
import { Icon } from "@/components/Icon";
import { Modal } from "@/components/ui";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { useAutoResizeTextarea } from "@/lib/useAutoResizeTextarea";
import { AGENTS, useChat } from "@/lib/useChat";
import { groupCourses, currentMember } from "@/lib/courseGroups";

const EXAMPLES = [
  "What mistakes do I keep making in AP Calculus?",
  "Show every assignment related to photosynthesis.",
  "What did I learn before Biology Quiz 3?",
  "What feedback has my English teacher repeated this year?",
];

type Conversation = {
  id: string;
  title: string | null;
  agent: string;
  project_id: string | null;
  course_id: string | null;
  pinned: boolean;
  tags: string[];
  archived: boolean;
  updated_at: string;
};
type Project = { id: string; name: string; color?: string | null; instructions?: string | null };

export default function AskAtlasPage() {
  // "all" = unscoped (the original global behavior); a class id scopes both
  // the conversation (see useChat's courseId) and reveals that class's
  // folders below; "general" just reveals the General folders (there's no
  // class to scope the chat itself to) -- see FolderPane.
  const [scope, setScope] = useState<string>("all");
  const scopeCourseId = scope !== "all" && scope !== "general" ? scope : null;
  const chat = useChat(loadConversations, scopeCourseId);
  const [input, setInput] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [courses, setCourses] = useState<any[]>([]);
  const [showFolders, setShowFolders] = useState(false);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  // Per-scope-section "show all" toggle -- lifted up here (rather than local
  // state inside the section component) because that component is redefined
  // every render, which would otherwise silently reset "show more" back to
  // collapsed on any unrelated parent re-render.
  const [showAllByScope, setShowAllByScope] = useState<Record<string, boolean>>({});
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useAutoResizeTextarea(input);

  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<Project | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const [instructionsTarget, setInstructionsTarget] = useState<Project | null>(null);
  const [instructionsValue, setInstructionsValue] = useState("");
  const [savingInstructions, setSavingInstructions] = useState(false);
  const [addTagTarget, setAddTagTarget] = useState<Conversation | null>(null);
  const [tagValue, setTagValue] = useState("");
  const [addingTag, setAddingTag] = useState(false);

  async function loadConversations() {
    try {
      setConversations((await apiGet("/agents/conversations")) ?? []);
    } catch {
      /* ignore */
    }
  }
  async function loadProjects() {
    try {
      setProjects((await apiGet("/chat-projects")) ?? []);
    } catch {
      /* ignore */
    }
  }
  async function loadCourses() {
    try {
      setCourses((await apiGet("/courses")) ?? []);
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    loadConversations();
    loadProjects();
    loadCourses();
  }, []);

  useEffect(() => {
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, [chat.messages]);

  function submit() {
    const t = input.trim();
    if (!t) return;
    setInput("");
    chat.send(t);
  }

  function openNewProject() {
    setNewProjectName("");
    setNewProjectOpen(true);
  }

  async function submitNewProject() {
    const name = newProjectName.trim();
    if (!name) return;
    setCreatingProject(true);
    try {
      await apiPost("/chat-projects", { name });
      setNewProjectOpen(false);
      loadProjects();
    } finally {
      setCreatingProject(false);
    }
  }

  async function confirmDeleteProject() {
    if (!deleteProjectTarget) return;
    setDeletingProject(true);
    try {
      await apiDelete(`/chat-projects/${deleteProjectTarget.id}`);
      setDeleteProjectTarget(null);
      loadProjects();
      loadConversations();
    } finally {
      setDeletingProject(false);
    }
  }

  function openEditInstructions(p: Project) {
    setInstructionsValue(p.instructions ?? "");
    setInstructionsTarget(p);
  }

  async function submitEditInstructions() {
    if (!instructionsTarget) return;
    setSavingInstructions(true);
    try {
      await apiPatch(`/chat-projects/${instructionsTarget.id}`, { instructions: instructionsValue });
      setInstructionsTarget(null);
      loadProjects();
    } finally {
      setSavingInstructions(false);
    }
  }

  async function patchConv(id: string, body: any) {
    await apiPatch(`/agents/conversations/${id}`, body);
    setMenuId(null);
    loadConversations();
  }
  async function delConv(id: string) {
    await apiDelete(`/agents/conversations/${id}`);
    setMenuId(null);
    if (chat.conv === id) chat.reset();
    loadConversations();
  }
  function openAddTag(c: Conversation) {
    setTagValue("");
    setAddTagTarget(c);
    setMenuId(null);
  }

  async function submitAddTag() {
    if (!addTagTarget) return;
    const tag = tagValue.trim();
    if (!tag) return;
    setAddingTag(true);
    try {
      const tags = Array.from(new Set([...(addTagTarget.tags ?? []), tag]));
      await patchConv(addTagTarget.id, { tags });
      setAddTagTarget(null);
    } finally {
      setAddingTag(false);
    }
  }

  const allTags = useMemo(
    () => Array.from(new Set(conversations.flatMap((c) => c.tags ?? []))).sort(),
    [conversations]
  );

  const visible = conversations.filter(
    (c) => (showArchived ? c.archived : !c.archived) && (!filterTag || (c.tags ?? []).includes(filterTag))
  );
  const byProject = (pid: string | null) => visible.filter((c) => (c.project_id ?? null) === pid);
  // Chats not filed under any project, further split by class (course_id
  // null = General) -- used to build the sidebar's per-class sections.
  const ungroupedByCourse = (courseId: string | null) =>
    byProject(null).filter((c) => (c.course_id ?? null) === courseId);
  // Same, but for a class split into linked semester rows (see
  // lib/courseGroups) -- a chat scoped to *either* half still belongs to
  // the one merged section, or "calc"/"bio"/"physics" (all semester-split
  // subjects) would keep showing as two separate sections in the sidebar.
  const ungroupedByGroup = (courseIds: string[]) =>
    byProject(null).filter((c) => c.course_id != null && courseIds.includes(c.course_id));
  // The 3 most recently updated chats, across every scope/project -- shown
  // in their own always-visible "Recent" section up top so a chat filed
  // under a class (or a project) doesn't need its section expanded to be
  // found quickly. Additive, not a replacement: a recent chat still also
  // shows up in its normal class/project section below.
  const mostRecentChats = [...visible]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 3);

  function ChatRow({ c }: { c: Conversation }) {
    const active = chat.conv === c.id;
    const open = menuId === c.id;
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      if (!open) return;
      const onClick = (e: MouseEvent) => {
        if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuId(null);
      };
      window.addEventListener("mousedown", onClick);
      return () => window.removeEventListener("mousedown", onClick);
    }, [open]);

    return (
      <div className="relative group" ref={menuRef}>
        <button
          onClick={() => chat.openConversation(c.id, c.agent)}
          className={`w-full text-left pl-3 pr-8 py-2 rounded-lg text-sm transition-colors truncate ${
            active
              ? "bg-atlas-accent/10 text-atlas-text border border-atlas-accent/40"
              : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
          }`}
          title={c.title ?? "Conversation"}
        >
          {c.pinned && <Icon name="pin" className="inline w-3 h-3 mr-1 mb-0.5" />}
          {c.title || "Untitled chat"}
          {c.tags?.length ? (
            <span className="ml-1 text-[10px] text-atlas-accent2">#{c.tags[0]}{c.tags.length > 1 ? "…" : ""}</span>
          ) : null}
        </button>
        <button
          onClick={() => setMenuId(open ? null : c.id)}
          aria-label="Conversation options"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-atlas-muted hover:text-atlas-text opacity-0 group-hover:opacity-100 px-1"
        >
          <Icon name="moreVertical" className="w-4 h-4" />
        </button>
        {open && (
          <div className="absolute right-1 top-full z-30 mt-1 w-52 max-h-72 overflow-y-auto rounded-xl border border-atlas-border bg-atlas-panel shadow-soft p-1 text-sm animate-fade-in">
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 flex items-center gap-1.5"
              onClick={() => patchConv(c.id, { pinned: !c.pinned })}>
              {!c.pinned && <Icon name="pin" className="w-3.5 h-3.5" />}
              {c.pinned ? "Unpin" : "Pin"}
            </button>
            <div className="border-t border-atlas-border my-1" />
            <div className="px-2 py-1 text-[11px] uppercase text-atlas-muted">Move to class</div>
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => patchConv(c.id, { course_id: null })}>General</button>
            {courseGroupList.map((g) => (
              <button key={g.key} className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 truncate"
                onClick={() => patchConv(c.id, { course_id: currentMember(g).id })}>{g.primary.name}</button>
            ))}
            <div className="border-t border-atlas-border my-1" />
            <div className="px-2 py-1 text-[11px] uppercase text-atlas-muted">Move to project</div>
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => patchConv(c.id, { project_id: null })}>No project</button>
            {projects.map((p) => (
              <button key={p.id} className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 truncate"
                onClick={() => patchConv(c.id, { project_id: p.id })}>{p.name}</button>
            ))}
            <div className="border-t border-atlas-border my-1" />
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => openAddTag(c)}>Add tag…</button>
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => patchConv(c.id, { archived: !c.archived })}>
              {c.archived ? "Unarchive" : "Archive"}
            </button>
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 text-atlas-bad"
              onClick={() => delConv(c.id)}>Delete</button>
          </div>
        )}
      </div>
    );
  }

  // A class/General section of the sidebar -- pinned chats always show,
  // then the 3 most recent (with a "show more" toggle for the rest). Its
  // header doubles as the scope selector (same `scope` state the info card
  // above and useChat's retrieval scoping both read) -- opening "AP
  // Biology" here both reveals its chats and scopes the composer to it.
  function ScopeSection({ id, label, chats }: { id: string; label: string; chats: Conversation[] }) {
    const isOpen = scope === id;
    const pinnedChats = chats.filter((c) => c.pinned);
    const rest = chats.filter((c) => !c.pinned);
    const showAll = !!showAllByScope[id];
    const visibleRest = showAll ? rest : rest.slice(0, 3);
    return (
      <div>
        <button
          onClick={() => setScope(isOpen ? "all" : id)}
          className={`w-full flex items-center justify-between gap-1 px-2 py-1.5 rounded-lg text-sm transition-colors ${
            isOpen ? "bg-atlas-accent/10 text-atlas-accent" : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
          }`}
        >
          <span className="truncate flex items-center gap-1">
            <Icon name={isOpen ? "chevronDown" : "chevronRight"} className="w-3.5 h-3.5 shrink-0" />
            {label}
          </span>
          <span className="text-[10px] shrink-0">{chats.length || ""}</span>
        </button>
        {isOpen && (
          <div className="space-y-0.5 mt-0.5 ml-1">
            {!chats.length && <div className="text-xs text-atlas-muted px-3 py-1.5">No chats here yet.</div>}
            {pinnedChats.map((c) => <ChatRow key={c.id} c={c} />)}
            {visibleRest.map((c) => <ChatRow key={c.id} c={c} />)}
            {rest.length > 3 && (
              <button
                className="text-xs text-atlas-muted hover:text-atlas-text px-3 py-1"
                onClick={() => setShowAllByScope((m) => ({ ...m, [id]: !m[id] }))}
              >
                {showAll ? "Show less" : `Show ${rest.length - 3} more`}
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  const activeAgent = AGENTS.find((a) => a.id === chat.agent);
  const activeCourses = courses.filter((c) => c.is_active !== false);
  // A class split into linked semester rows (see lib/courseGroups) is still
  // one real class -- group them so it shows up as a single sidebar
  // section / move-to-class option instead of duplicated once per
  // semester row.
  const courseGroupList = groupCourses(activeCourses);
  const scopeGroup = courseGroupList.find((g) => g.key === scope);
  const scopeName = scope === "general" ? "General" : courses.find((c) => c.id === scope)?.name ?? "this class";

  return (
    <AppShell
      title="Ask Atlas"
      subtitle="Chat with your specialists — grounded in your real academic life"
      actions={<button className="btn-ghost" onClick={chat.reset}>New chat</button>}
      fullWidth
    >
      {scope !== "all" && (
        <div className="card mb-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm">
              {scope === "general"
                ? "Chatting isn't scoped by General — it's a folder for documents with no class."
                : `This chat is scoped to ${scopeName} — grounded in just its assignments, grades, and documents.`}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button className="btn-ghost text-xs" onClick={() => setShowFolders((s) => !s)}>
                {showFolders ? "Hide folders" : "Show folders"}
              </button>
              <button className="btn-ghost text-xs" onClick={() => setScope("all")}>
                <Icon name="close" className="w-3.5 h-3.5" /> Clear scope
              </button>
            </div>
          </div>
          {showFolders && (
            <FolderPane
              courseId={
                scope === "general" ? null : scopeGroup?.members.map((m) => m.id).join(",") ?? scope
              }
              uploadCourseId={
                scope === "general" ? null : scopeGroup ? currentMember(scopeGroup).id : scope
              }
            />
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[16rem_1fr] gap-4">
        {/* Projects / history rail */}
        <aside className="hidden lg:flex flex-col gap-2 min-h-0">
          <div className="flex gap-2">
            <button className="btn-primary flex-1 !py-1.5 text-sm" onClick={chat.reset}>+ New chat</button>
            <button className="btn-ghost !py-1.5 text-sm !px-2.5" onClick={openNewProject} title="New project" aria-label="New project">
              <Icon name="folder" className="w-4 h-4" />
            </button>
          </div>

          {allTags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {allTags.map((t) => (
                <button key={t}
                  onClick={() => setFilterTag(filterTag === t ? null : t)}
                  className={`pill ${filterTag === t ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}>
                  #{t}
                </button>
              ))}
            </div>
          )}

          <div className="space-y-3 overflow-auto max-h-[60vh] pr-1 mt-1">
            {/* Recent -- the 3 most recently updated chats regardless of
                scope/project, so one buried in a class section is still
                one click away. Additive: a chat listed here still also
                shows up in its normal project/class section below. */}
            {mostRecentChats.length > 0 && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1">Recent</div>
                <div className="space-y-0.5">
                  {mostRecentChats.map((c) => <ChatRow key={c.id} c={c} />)}
                </div>
              </div>
            )}

            {/* Projects */}
            {projects.map((p) => {
              const chats = byProject(p.id);
              if (!chats.length && (filterTag || showArchived)) return null;
              return (
                <div key={p.id} className="group">
                  <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1 flex items-center gap-1">
                    <Icon name="folder" className="w-3 h-3 shrink-0" /> {p.name}
                    <button
                      className="opacity-0 group-hover:opacity-100 normal-case text-atlas-muted hover:text-atlas-text"
                      title="Edit custom instructions for this project"
                      aria-label={`Edit instructions for ${p.name}`}
                      onClick={() => openEditInstructions(p)}
                    >
                      <Icon name="pencil" className="w-3.5 h-3.5" />
                    </button>
                    <button
                      className="ml-auto opacity-0 group-hover:opacity-100 normal-case text-atlas-muted hover:text-atlas-bad"
                      title="Delete project (chats move back to ungrouped)"
                      aria-label={`Delete project ${p.name}`}
                      onClick={() => setDeleteProjectTarget(p)}
                    >
                      <Icon name="close" className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="space-y-0.5">
                    {chats.length ? chats.map((c) => <ChatRow key={c.id} c={c} />)
                      : <div className="text-xs text-atlas-muted px-3 py-1">Empty</div>}
                  </div>
                </div>
              );
            })}

            {/* Auto-sorted by class -- a chat with no project files itself
                here based on its own course_id (set from whatever scope was
                active when it started, or guessed shortly after -- see
                app.agents.chat_scope). */}
            <div>
              {projects.length > 0 && (
                <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1">
                  {showArchived ? "Archived" : "By class"}
                </div>
              )}
              <div className="space-y-1">
                {courseGroupList.map((g) => (
                  <ScopeSection
                    key={g.key} id={g.key} label={g.primary.name}
                    chats={ungroupedByGroup(g.members.map((m) => m.id))}
                  />
                ))}
                <ScopeSection id="general" label="General" chats={ungroupedByCourse(null)} />
              </div>
            </div>
          </div>

          <button
            onClick={() => setShowArchived((s) => !s)}
            className="text-xs text-atlas-muted hover:text-atlas-text text-left px-1 mt-auto pt-2"
          >
            {showArchived ? "← Back to active chats" : "View archived"}
          </button>
        </aside>

        {/* Conversation column — Claude-style */}
        <div className="flex flex-col min-h-[64vh]">
          <div className="flex-1 overflow-auto">
            <div className="w-full px-1 py-2 space-y-6">
              {!chat.messages.length && (
                <div className="text-center py-12">
                  <div className="text-sm text-atlas-muted mb-4">
                    Ask the <span className="text-atlas-text font-medium">{activeAgent?.label}</span> anything —
                    {" "}{activeAgent?.blurb.toLowerCase()}.
                  </div>
                  <div className="flex flex-wrap gap-2 justify-center">
                    {EXAMPLES.map((ex) => (
                      <button key={ex}
                        className="pill text-atlas-muted hover:text-atlas-accent hover:border-atlas-accent/50"
                        onClick={() => chat.send(ex)}>
                        {ex}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {chat.messages.map((m, i) => {
                const isLastAssistant = m.role === "assistant" && i === chat.messages.length - 1 && !chat.busy;
                return (
                  <div key={i} className={m.role === "user" ? "flex justify-end" : "animate-fade-in"}>
                    {m.role === "user" ? (
                      <div className="max-w-[80%] rounded-2xl px-4 py-2.5 text-sm bg-atlas-accent text-white">
                        {m.attachmentName && (
                          <div className="text-xs text-white/80 mb-1 flex items-center gap-1">
                            <Icon name="paperclip" className="w-3 h-3 shrink-0" /> {m.attachmentName}
                          </div>
                        )}
                        {m.content}
                      </div>
                    ) : (
                      <div>
                        <ChatMessageContent content={m.content} />
                        {m.pendingAction && (
                          <div className="flex gap-2 mt-2">
                            <button className="btn-primary !py-1.5 !px-3 text-sm" onClick={() => chat.confirmAction(i)}>
                              Confirm
                            </button>
                            <button className="btn-ghost !py-1.5 !px-3 text-sm" onClick={() => chat.dismissAction(i)}>
                              Cancel
                            </button>
                          </div>
                        )}
                        {!m.pendingAction && (
                          <ChatMessageActions
                            content={m.content}
                            feedback={m.feedback}
                            onFeedback={m.id ? (rating) => chat.setFeedback(i, rating) : undefined}
                            onRegenerate={isLastAssistant ? chat.regenerate : undefined}
                          />
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              {chat.busy && <ThinkingDots label={`${activeAgent?.label} is thinking`} />}
              <div ref={endRef} />
            </div>
          </div>

          {/* Composer */}
          <div className="w-full pt-3">
            <div className="rounded-2xl border border-atlas-border bg-atlas-panel2 p-2.5 shadow-soft">
              <textarea
                ref={textareaRef}
                className="w-full bg-transparent outline-none text-sm resize-none px-2 pt-1 min-h-[44px] max-h-40 overflow-y-auto placeholder:text-atlas-muted"
                placeholder={`Message the ${activeAgent?.label}…`}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
                }}
                rows={1}
              />
              {chat.attachment && (
                <div className="px-2 pb-1.5">
                  <ChatAttachButton
                    attachment={chat.attachment}
                    onAttach={chat.setAttachment}
                    onClear={() => chat.setAttachment(null)}
                  />
                </div>
              )}
              <div className="flex items-center justify-between mt-1">
                <div className="flex items-center gap-1.5">
                  <AgentPicker agent={chat.agent} onChange={chat.setAgent} up />
                  {!chat.attachment && (
                    <ChatAttachButton
                      attachment={null}
                      onAttach={chat.setAttachment}
                      onClear={() => chat.setAttachment(null)}
                    />
                  )}
                </div>
                <button className="btn-primary !px-4 !py-1.5" onClick={submit} disabled={chat.busy}>
                  Send
                </button>
              </div>
            </div>
            <div className="text-[11px] text-atlas-muted text-center mt-1.5">
              Enter to send · Shift+Enter for a new line
            </div>
          </div>
        </div>
      </div>

      <Modal
        open={newProjectOpen}
        onClose={() => setNewProjectOpen(false)}
        title="New project"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setNewProjectOpen(false)}>Cancel</button>
            <button className="btn-primary" disabled={creatingProject || !newProjectName.trim()} onClick={submitNewProject}>
              {creatingProject ? "Creating…" : "Create"}
            </button>
          </>
        }
      >
        <input
          className="input"
          autoFocus
          placeholder="e.g. AP Biology, Calc Unit 3"
          value={newProjectName}
          onChange={(e) => setNewProjectName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitNewProject()}
        />
      </Modal>

      <Modal
        open={!!deleteProjectTarget}
        onClose={() => setDeleteProjectTarget(null)}
        title={`Delete "${deleteProjectTarget?.name}"?`}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setDeleteProjectTarget(null)}>Cancel</button>
            <button
              className="btn-primary !bg-atlas-bad hover:!brightness-110"
              disabled={deletingProject}
              onClick={confirmDeleteProject}
            >
              {deletingProject ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        <p className="text-sm text-atlas-muted">Its chats move back to ungrouped — they aren&apos;t deleted.</p>
      </Modal>

      <Modal
        open={!!instructionsTarget}
        onClose={() => setInstructionsTarget(null)}
        title={`Custom instructions — ${instructionsTarget?.name}`}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setInstructionsTarget(null)}>Cancel</button>
            <button className="btn-primary" disabled={savingInstructions} onClick={submitEditInstructions}>
              {savingInstructions ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        <div className="space-y-2">
          <p className="text-xs text-atlas-muted">
            Applied to every chat filed under this project (e.g. &quot;always show your work&quot;,
            &quot;focus on MLA citations&quot;). Leave blank to clear.
          </p>
          <textarea
            className="input"
            rows={4}
            autoFocus
            value={instructionsValue}
            onChange={(e) => setInstructionsValue(e.target.value)}
          />
        </div>
      </Modal>

      <Modal
        open={!!addTagTarget}
        onClose={() => setAddTagTarget(null)}
        title="Add a tag"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setAddTagTarget(null)}>Cancel</button>
            <button className="btn-primary" disabled={addingTag || !tagValue.trim()} onClick={submitAddTag}>
              {addingTag ? "Adding…" : "Add"}
            </button>
          </>
        }
      >
        <input
          className="input"
          autoFocus
          placeholder="Class, subject, or unit…"
          value={tagValue}
          onChange={(e) => setTagValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitAddTag()}
        />
      </Modal>
    </AppShell>
  );
}
