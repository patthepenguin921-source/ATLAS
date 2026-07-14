"use client";

import { useState } from "react";
import { apiPost, apiGet } from "@/lib/api";

export type Msg = { role: "user" | "assistant"; content: string };

export const AGENTS = [
  { id: "general", label: "Atlas", blurb: "Coordinates everything" },
  { id: "planner", label: "Planner", blurb: "Schedules & priorities" },
  { id: "tutor", label: "Tutor", blurb: "Explains & quizzes you" },
  { id: "analyst", label: "Analyst", blurb: "Finds performance patterns" },
  { id: "coach", label: "Coach", blurb: "Accountability & reviews" },
];

/** Shared chat behavior for the Ask Atlas page and the floating popup. */
export function useChat(onNewConversation?: () => void) {
  const [agent, setAgent] = useState("general");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [conv, setConv] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setConv(null);
    setMessages([]);
  }

  async function openConversation(id: string, fallbackAgent = "general") {
    setConv(id);
    setAgent(fallbackAgent);
    setMessages([]);
    try {
      const msgs = await apiGet(`/agents/conversations/${id}/messages`);
      setMessages(
        (msgs ?? [])
          .filter((m: any) => m.role === "user" || m.role === "assistant")
          .map((m: any) => ({ role: m.role, content: m.content }))
      );
    } catch {
      /* ignore */
    }
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: "user", content: message }]);
    setBusy(true);
    try {
      const isNew = !conv;
      const r = await apiPost("/agents/chat", { message, agent, conversation_id: conv });
      setConv(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.reply }]);
      if (isNew) onNewConversation?.();
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return { agent, setAgent, messages, conv, busy, reset, openConversation, send };
}
