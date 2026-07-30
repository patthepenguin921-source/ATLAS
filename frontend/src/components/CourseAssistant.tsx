"use client";

import { useEffect, useRef, useState } from "react";
import { useChat } from "@/lib/useChat";

/** An "Ask Atlas about this class" panel embedded directly on a course
 *  page — unlike the floating chat (always global), every turn here is
 *  scoped to this one course (see `useChat`'s `courseId` param / the
 *  backend's `ChatRequest.course_id`), so retrieval and the model's
 *  answers stay anchored to what's actually relevant on this page. */
export function CourseAssistant({ courseId, courseName }: { courseId: string; courseName: string }) {
  const [input, setInput] = useState("");
  const { messages, busy, send, reset } = useChat(undefined, courseId);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (!t) return;
    setInput("");
    send(t);
  }

  return (
    <div className="card flex flex-col h-96">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-medium">Ask Atlas about {courseName}</div>
        <button onClick={reset} title="New chat" className="text-atlas-muted hover:text-atlas-text text-xs">
          New chat
        </button>
      </div>
      <div className="flex-1 overflow-auto space-y-2 pr-1">
        {!messages.length && (
          <div className="text-xs text-atlas-muted text-center py-8">
            Ask about this class's assignments, grades, or documents.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <div className="max-w-[85%] rounded-2xl px-3 py-1.5 text-sm bg-atlas-accent text-white">
                {m.content}
              </div>
            ) : (
              <div className="text-sm whitespace-pre-wrap leading-relaxed">{m.content}</div>
            )}
          </div>
        ))}
        {busy && <div className="text-xs text-atlas-muted">Thinking…</div>}
        <div ref={endRef} />
      </div>
      <form onSubmit={submit} className="flex items-center gap-2 mt-2 pt-2 border-t border-atlas-border">
        <input
          className="input !py-1.5"
          placeholder={`Ask about ${courseName}…`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button className="btn-primary !px-3 !py-1.5" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
