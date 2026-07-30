"use client";

import { useState } from "react";
import { apiPost, apiGet } from "@/lib/api";

export type PendingAction = {
  name: string;
  arguments: Record<string, any>;
  description?: string | null;
};

export type Msg = {
  role: "user" | "assistant";
  content: string;
  /** A destructive action (delete_*) the agent proposed on this turn but
   *  hasn't performed yet -- see app.agents.tools. Cleared once confirmed
   *  or dismissed so the button can't be clicked twice. */
  pendingAction?: PendingAction | null;
  /** Name of a file attached "for this conversation only" on this turn --
   *  display only, the extracted text already went to the backend as part
   *  of the request that produced this message. */
  attachmentName?: string | null;
};

/** A file attached via the composer's "use for this conversation only"
 *  option -- its extracted text rides along on the next `send()` call and
 *  is never persisted server-side (see POST /documents/extract-temp). A
 *  file saved via the composer's other option ("Save to Documents") never
 *  becomes one of these; it's a plain, independent upload. */
export type PendingAttachment = { filename: string; text: string; truncated: boolean };

export const AGENTS = [
  { id: "general", label: "Atlas", blurb: "Coordinates everything" },
  { id: "planner", label: "Planner", blurb: "Schedules & priorities" },
  { id: "tutor", label: "Tutor", blurb: "Explains & quizzes you" },
  { id: "analyst", label: "Analyst", blurb: "Finds performance patterns" },
  { id: "coach", label: "Coach", blurb: "Accountability & reviews" },
];

/** Shared chat behavior for the Ask Atlas page, the floating popup, and a
 *  course page's embedded assistant. Passing `courseId` scopes every turn
 *  to that one class (see `ChatRequest.course_id`) -- retrieval narrows to
 *  its assignments/grades/documents and the model is told it's answering
 *  from that class's own page, without losing the rest of the student's
 *  record for a cross-class question. */
export function useChat(onNewConversation?: () => void, courseId?: string | null) {
  const [agent, setAgent] = useState("general");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [conv, setConv] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attachment, setAttachment] = useState<PendingAttachment | null>(null);

  function reset() {
    setConv(null);
    setMessages([]);
    setAttachment(null);
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
    const pending = attachment;
    setAttachment(null);
    setMessages((m) => [...m, { role: "user", content: message, attachmentName: pending?.filename ?? null }]);
    setBusy(true);
    try {
      const isNew = !conv;
      const r = await apiPost("/agents/chat", {
        message, agent, conversation_id: conv,
        attachment_text: pending?.text, attachment_filename: pending?.filename,
        course_id: courseId || undefined,
      });
      setConv(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.reply, pendingAction: r.pending_action ?? null }]);
      if (isNew) onNewConversation?.();
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  // Actually performs a destructive action a message's `pendingAction`
  // described, once the student clicks Confirm — see
  // `POST /agents/actions/confirm` and `app.agents.tools`. Clears the
  // pending action on that message either way so the button can't fire twice.
  async function confirmAction(index: number) {
    const target = messages[index];
    if (!target?.pendingAction || busy) return;
    const { name, arguments: args } = target.pendingAction;
    setMessages((m) => m.map((msg, i) => (i === index ? { ...msg, pendingAction: null } : msg)));
    setBusy(true);
    try {
      const r = await apiPost("/agents/actions/confirm", { name, arguments: args, conversation_id: conv });
      setMessages((m) => [...m, { role: "assistant", content: r.reply }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  function dismissAction(index: number) {
    setMessages((m) => m.map((msg, i) => (i === index ? { ...msg, pendingAction: null } : msg)));
  }

  return {
    agent, setAgent, messages, conv, busy, reset, openConversation, send, confirmAction, dismissAction,
    attachment, setAttachment,
  };
}
