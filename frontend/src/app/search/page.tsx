"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { AgentPicker } from "@/components/AgentPicker";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
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
  tags: string[];
  archived: boolean;
  updated_at: string;
};
type Project = { id: string; name: string; color?: string | null };

export default function AskAtlasPage() {
  const chat = useChat(loadConversations);
  const [input, setInput] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

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
  useEffect(() => {
    loadConversations();
    loadProjects();
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
          <div className="absolute right-1 top-full z-30 mt-1 w-48 rounded-xl border border-atlas-border bg-atlas-panel shadow-soft p-1 text-sm animate-fade-in">
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

  const activeAgent = AGENTS.find((a) => a.id === chat.agent);

  return (
    <AppShell
      title="Ask Atlas"
      subtitle="Chat with your specialists — grounded in your real academic life"
      actions={<button className="btn-ghost" onClick={chat.reset}>New chat</button>}
    >
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
                <div key={p.id}>
                  <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1 flex items-center gap-1">
                    <span>📁</span> {p.name}
                  </div>
                  <div className="space-y-0.5">
                    {chats.length ? chats.map((c) => <ChatRow key={c.id} c={c} />)
                      : <div className="text-xs text-atlas-muted px-3 py-1">Empty</div>}
                  </div>
                </div>
              );
            })}

            {/* Ungrouped */}
            <div>
              {projects.length > 0 && (
                <div className="text-[11px] uppercase tracking-wide text-atlas-muted px-1 mb-1">
                  {showArchived ? "Archived" : "Chats"}
                </div>
              )}
              <div className="space-y-0.5">
                {byProject(null).length ? byProject(null).map((c) => <ChatRow key={c.id} c={c} />)
                  : <div className="text-xs text-atlas-muted px-3 py-2">
                      {showArchived ? "Nothing archived." : "Your chats will appear here."}
                    </div>}
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
            <div className="max-w-2xl mx-auto px-1 py-2 space-y-6">
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
              {chat.messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "flex justify-end" : "animate-fade-in"}>
                  {m.role === "user" ? (
                    <div className="max-w-[80%] rounded-2xl px-4 py-2.5 text-sm bg-atlas-accent text-white">
                      {m.content}
                    </div>
                  ) : (
                    <div className="text-[15px] leading-relaxed whitespace-pre-wrap">{m.content}</div>
                  )}
                </div>
              ))}
              {chat.busy && <div className="text-xs text-atlas-muted animate-fade-in">{activeAgent?.label} is thinking…</div>}
              <div ref={endRef} />
            </div>
          </div>

          {/* Composer */}
          <div className="max-w-2xl mx-auto w-full pt-3">
            <div className="rounded-2xl border border-atlas-border bg-atlas-panel2 p-2.5 shadow-soft">
              <textarea
                className="w-full bg-transparent outline-none text-sm resize-none px-2 pt-1 min-h-[44px] max-h-40 placeholder:text-atlas-muted"
                placeholder={`Message the ${activeAgent?.label}…`}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
                }}
                rows={1}
              />
              <div className="flex items-center justify-between mt-1">
                <AgentPicker agent={chat.agent} onChange={chat.setAgent} up />
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
