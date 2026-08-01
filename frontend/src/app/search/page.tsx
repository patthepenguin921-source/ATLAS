"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { AgentPicker } from "@/components/AgentPicker";
import { ChatAttachButton } from "@/components/ChatAttachButton";
import { ChatMessageActions } from "@/components/ChatMessageActions";
import { ChatMessageContent } from "@/components/ChatMessageContent";
import { FolderPane } from "@/components/FolderPane";
import { ThinkingDots } from "@/components/ThinkingDots";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { useAutoResizeTextarea } from "@/lib/useAutoResizeTextarea";
import { AGENTS, useChat } from "@/lib/useChat";

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

  async function newProject() {
    const name = window.prompt("Project name (e.g. AP Biology, Calc Unit 3)");
    if (!name?.trim()) return;
    await apiPost("/chat-projects", { name: name.trim() });
    loadProjects();
  }

  async function deleteProject(p: Project) {
    if (!window.confirm(`Delete "${p.name}"? Its chats move back to ungrouped, not deleted.`)) return;
    await apiDelete(`/chat-projects/${p.id}`);
    loadProjects();
    loadConversations();
  }

  async function editProjectInstructions(p: Project) {
    const next = window.prompt(
      `Custom instructions for "${p.name}" — applied to every chat filed under this project ` +
        `(e.g. "always show your work", "focus on MLA citations"). Leave blank to clear:`,
      p.instructions ?? ""
    );
    if (next === null) return; // cancelled
    await apiPatch(`/chat-projects/${p.id}`, { instructions: next });
    loadProjects();
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
  async function addTag(c: Conversation) {
    const tag = window.prompt("Add a tag (class, subject, or unit):");
    if (!tag?.trim()) return;
    const tags = Array.from(new Set([...(c.tags ?? []), tag.trim()]));
    patchConv(c.id, { tags });
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

  function ChatRow({ c }: { c: Conversation }) {
    const active = chat.conv === c.id;
    return (
      <div className="relative group">
        <button
          onClick={() => chat.openConversation(c.id, c.agent)}
          className={`w-full text-left pl-3 pr-8 py-2 rounded-lg text-sm transition-colors truncate ${
            active
              ? "bg-atlas-accent/10 text-atlas-text border border-atlas-accent/40"
              : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
          }`}
          title={c.title ?? "Conversation"}
        >
          {c.pinned && <span className="mr-1" title="Pinned">📌</span>}
          {c.title || "Untitled chat"}
          {c.tags?.length ? (
            <span className="ml-1 text-[10px] text-atlas-accent2">#{c.tags[0]}{c.tags.length > 1 ? "…" : ""}</span>
          ) : null}
        </button>
        <button
          onClick={() => setMenuId(menuId === c.id ? null : c.id)}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-atlas-muted hover:text-atlas-text opacity-0 group-hover:opacity-100 px-1"
        >
          ⋯
        </button>
        {menuId === c.id && (
          <div className="absolute right-1 top-full z-30 mt-1 w-52 max-h-72 overflow-y-auto rounded-xl border border-atlas-border bg-atlas-panel shadow-soft p-1 text-sm animate-fade-in">
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => patchConv(c.id, { pinned: !c.pinned })}>
              {c.pinned ? "Unpin" : "📌 Pin"}
            </button>
            <div className="border-t border-atlas-border my-1" />
            <div className="px-2 py-1 text-[11px] uppercase text-atlas-muted">Move to class</div>
            <button className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2"
              onClick={() => patchConv(c.id, { course_id: null })}>General</button>
            {activeCourses.map((cc) => (
              <button key={cc.id} className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-atlas-panel2 truncate"
                onClick={() => patchConv(c.id, { course_id: cc.id })}>{cc.name}</button>
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
              onClick={() => addTag(c)}>Add tag…</button>
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
          <span className="truncate">{isOpen ? "▾" : "▸"} {label}</span>
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
                ✕ Clear scope
              </button>
            </div>
          </div>
          {showFolders && <FolderPane courseId={scope === "general" ? null : scope} />}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[16rem_1fr] gap-4">
        {/* Projects / history rail */}
        <aside className="hidden lg:flex flex-col gap-2 min-h-0">
          <div className="flex gap-2">
            <button className="btn-primary flex-1 !py-1.5 text-sm" onClick={chat.reset}>+ New chat</button>
            <button className="btn-ghost !py-1.5 text-sm" onClick={newProject} title="New project">📁</button>
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
            {/* Projects */}
            {projects.map((p) => {
              const chats = byProject(p.id);
              if (!chats.length && (filterTag || showArchived)) return null;
              return (
                <div key={p.id} className="group">
                  <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1 flex items-center gap-1">
                    <span>📁</span> {p.name}
                    <button
                      className="opacity-0 group-hover:opacity-100 normal-case text-atlas-muted hover:text-atlas-text"
                      title="Edit custom instructions for this project"
                      onClick={() => editProjectInstructions(p)}
                    >
                      ✎
                    </button>
                    <button
                      className="ml-auto opacity-0 group-hover:opacity-100 normal-case text-atlas-muted hover:text-atlas-bad"
                      title="Delete project (chats move back to ungrouped)"
                      onClick={() => deleteProject(p)}
                    >
                      ×
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
                {activeCourses.map((c) => (
                  <ScopeSection key={c.id} id={c.id} label={c.name} chats={ungroupedByCourse(c.id)} />
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
                          <div className="text-xs text-white/80 mb-1">📎 {m.attachmentName}</div>
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
    </AppShell>
  );
}
