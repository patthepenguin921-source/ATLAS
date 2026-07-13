"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { apiGet, apiPost } from "@/lib/api";

const AGENTS = [
  { id: "general", label: "Atlas", blurb: "Coordinates everything" },
  { id: "planner", label: "Planner", blurb: "Schedules & priorities" },
  { id: "tutor", label: "Tutor", blurb: "Explains & quizzes you" },
  { id: "analyst", label: "Analyst", blurb: "Finds performance patterns" },
  { id: "coach", label: "Coach", blurb: "Accountability & reviews" },
];

const EXAMPLES = [
  "What mistakes do I keep making in AP Calculus?",
  "Show every assignment related to photosynthesis.",
  "What did I learn before Biology Quiz 3?",
  "What feedback has my English teacher repeated this year?",
];

type Msg = { role: "user" | "assistant"; content: string };
type Conversation = { id: string; title: string | null; agent: string; updated_at: string };

export default function AskAtlasPage() {
  const [agent, setAgent] = useState("general");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conv, setConv] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  async function loadConversations() {
    try {
      setConversations((await apiGet("/agents/conversations")) ?? []);
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    loadConversations();
  }, []);

  function scrollToEnd() {
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }

  function newChat() {
    setConv(null);
    setMessages([]);
    setInput("");
  }

  async function openConversation(c: Conversation) {
    setConv(c.id);
    setAgent(c.agent ?? "general");
    setMessages([]);
    try {
      const msgs = await apiGet(`/agents/conversations/${c.id}/messages`);
      setMessages(
        (msgs ?? [])
          .filter((m: any) => m.role === "user" || m.role === "assistant")
          .map((m: any) => ({ role: m.role, content: m.content }))
      );
      scrollToEnd();
    } catch {
      /* ignore */
    }
  }

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    setBusy(true);
    scrollToEnd();
    try {
      const r = await apiPost("/agents/chat", { message, agent, conversation_id: conv });
      const isNew = !conv;
      setConv(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.reply }]);
      if (isNew) loadConversations();
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
      scrollToEnd();
    }
  }

  const activeAgent = AGENTS.find((a) => a.id === agent);

  return (
    <AppShell
      title="Ask Atlas"
      subtitle="Chat with your specialists — grounded in your real academic life"
      actions={<button className="btn-ghost" onClick={newChat}>New chat</button>}
    >
      <div className="grid grid-cols-1 lg:grid-cols-[15rem_1fr] gap-4">
        {/* Conversation history rail */}
        <aside className="hidden lg:flex flex-col gap-2">
          <button className="btn-primary w-full" onClick={newChat}>+ New chat</button>
          <div className="text-xs uppercase tracking-wide text-atlas-muted mt-2 mb-1 px-1">
            History
          </div>
          <div className="space-y-1 overflow-auto max-h-[60vh] pr-1">
            {conversations.length === 0 && (
              <div className="text-xs text-atlas-muted px-1 py-2">
                Your chats will appear here.
              </div>
            )}
            {conversations.map((c) => (
              <button
                key={c.id}
                onClick={() => openConversation(c)}
                className={`w-full text-left px-3 py-2 rounded-xl text-sm transition-colors truncate ${
                  conv === c.id
                    ? "bg-atlas-accent/10 text-atlas-text border border-atlas-accent/40"
                    : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
                }`}
                title={c.title ?? "Conversation"}
              >
                <span className="text-[10px] uppercase text-atlas-accent2 mr-1.5">
                  {AGENTS.find((a) => a.id === c.agent)?.label ?? c.agent}
                </span>
                {c.title || "Untitled chat"}
              </button>
            ))}
          </div>
        </aside>

        {/* Chat column */}
        <div className="card min-h-[62vh] flex flex-col !p-0 overflow-hidden">
          {/* Agent picker */}
          <div className="flex items-center gap-1.5 p-3 border-b border-atlas-border overflow-x-auto">
            {AGENTS.map((a) => (
              <button
                key={a.id}
                onClick={() => setAgent(a.id)}
                title={a.blurb}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${
                  agent === a.id
                    ? "bg-atlas-accent text-atlas-bg"
                    : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-auto p-4 space-y-4">
            {!messages.length && (
              <div className="text-center py-12">
                <div className="text-sm text-atlas-muted mb-4">
                  Ask the <span className="text-atlas-text font-medium">{activeAgent?.label}</span> anything —
                  {" "}{activeAgent?.blurb.toLowerCase()}. Every reply is grounded in your courses, grades, and documents.
                </div>
                <div className="flex flex-wrap gap-2 justify-center max-w-xl mx-auto">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      className="pill text-atlas-muted hover:text-atlas-accent hover:border-atlas-accent/50"
                      onClick={() => send(ex)}
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap animate-fade-in ${
                    m.role === "user"
                      ? "bg-atlas-accent text-atlas-bg font-medium"
                      : "bg-atlas-panel2 border border-atlas-border"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex justify-start">
                <div className="bg-atlas-panel2 border border-atlas-border rounded-2xl px-4 py-2.5 text-xs text-atlas-muted">
                  {activeAgent?.label} is thinking…
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Composer */}
          <div className="flex gap-2 p-3 border-t border-atlas-border">
            <input
              className="input"
              placeholder={`Message the ${activeAgent?.label}…`}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
            />
            <button className="btn-primary shrink-0" onClick={() => send()} disabled={busy}>
              Send
            </button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
