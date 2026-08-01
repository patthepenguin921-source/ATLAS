"use client";

import { useEffect, useRef, useState } from "react";
import { useAutoResizeTextarea } from "@/lib/useAutoResizeTextarea";
import { useChat } from "@/lib/useChat";
import { ChatMessageActions } from "./ChatMessageActions";
import { ChatMessageContent } from "./ChatMessageContent";
import { ThinkingDots } from "./ThinkingDots";

/** An "Ask Atlas about this class" panel embedded directly on a course
 *  page — unlike the floating chat (always global), every turn here is
 *  scoped to this one course (see `useChat`'s `courseId` param / the
 *  backend's `ChatRequest.course_id`), so retrieval and the model's
 *  answers stay anchored to what's actually relevant on this page. */
export function CourseAssistant({ courseId, courseName }: { courseId: string; courseName: string }) {
  const [input, setInput] = useState("");
  const { messages, busy, send, reset, regenerate, setFeedback } = useChat(undefined, courseId);
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useAutoResizeTextarea(input);

  useEffect(() => {
    // Guarded on `messages.length` -- without it, this fires on the very
    // first render too (an empty `messages` array still counts as a
    // dependency change from "undefined" to "[]"), which pulls the whole
    // course page down to this panel's bottom the instant it mounts, every
    // time you open a class page.
    if (!messages.length) return;
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
        {messages.map((m, i) => {
          const isLastAssistant = m.role === "assistant" && i === messages.length - 1 && !busy;
          return (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              {m.role === "user" ? (
                <div className="max-w-[85%] rounded-2xl px-3 py-1.5 text-sm bg-atlas-accent text-white">
                  {m.content}
                </div>
              ) : (
                <div>
                  <ChatMessageContent content={m.content} className="text-sm leading-relaxed" />
                  <ChatMessageActions
                    content={m.content}
                    feedback={m.feedback}
                    onFeedback={m.id ? (rating) => setFeedback(i, rating) : undefined}
                    onRegenerate={isLastAssistant ? regenerate : undefined}
                  />
                </div>
              )}
            </div>
          );
        })}
        {busy && <ThinkingDots label="Thinking" />}
        <div ref={endRef} />
      </div>
      <form onSubmit={submit} className="flex items-end gap-2 mt-2 pt-2 border-t border-atlas-border">
        <textarea
          ref={textareaRef}
          className="input !py-1.5 flex-1 resize-none max-h-24 overflow-y-auto"
          placeholder={`Ask about ${courseName}…`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(e); }
          }}
          rows={1}
        />
        <button className="btn-primary !px-3 !py-1.5" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
