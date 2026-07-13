"use client";

import { useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { apiPost } from "@/lib/api";

const AGENTS = [
  { id: "general", label: "Atlas", blurb: "General — coordinates everything" },
  { id: "planner", label: "Planner", blurb: "Schedules & priorities" },
  { id: "tutor", label: "Tutor", blurb: "Explains & quizzes you" },
  { id: "analyst", label: "Analyst", blurb: "Finds performance patterns" },
  { id: "coach", label: "Coach", blurb: "Accountability & reviews" },
];

type Msg = { role: "user" | "assistant"; content: string };

export default function ChatPage() {
  const [agent, setAgent] = useState("general");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conv, setConv] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const r = await apiPost("/agents/chat", {
        message: text,
        agent,
        conversation_id: conv,
      });
      setConv(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.reply }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  }

  return (
    <AppShell title="Agents" subtitle="Five specialists, one shared memory">
      <div className="flex flex-wrap gap-2 mb-4">
        {AGENTS.map((a) => (
          <button
            key={a.id}
            onClick={() => { setAgent(a.id); setMessages([]); setConv(null); }}
            className={`card card-hover !py-2 !px-3 text-left ${
              agent === a.id ? "border-atlas-accent" : ""
            }`}
          >
            <div className="text-sm font-medium">{a.label}</div>
            <div className="text-[11px] text-atlas-muted">{a.blurb}</div>
          </button>
        ))}
      </div>

      <div className="card min-h-[50vh] flex flex-col">
        <div className="flex-1 space-y-4 overflow-auto">
          {!messages.length && (
            <div className="text-sm text-atlas-muted text-center py-16">
              Ask the {AGENTS.find((a) => a.id === agent)?.label} anything. Every reply is
              grounded in your real courses, grades, and documents.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-atlas-accent text-white"
                  : "bg-atlas-panel2 border border-atlas-border"
              }`}>
                {m.content}
              </div>
            </div>
          ))}
          {busy && <div className="text-xs text-atlas-muted">Atlas is thinking…</div>}
          <div ref={endRef} />
        </div>
        <div className="flex gap-2 mt-4 pt-4 border-t border-atlas-border">
          <input
            className="input"
            placeholder="Message…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <button className="btn-primary" onClick={send} disabled={busy}>Send</button>
        </div>
      </div>
    </AppShell>
  );
}
