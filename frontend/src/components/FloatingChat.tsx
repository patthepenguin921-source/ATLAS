"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAutoResizeTextarea } from "@/lib/useAutoResizeTextarea";
import { useChat } from "@/lib/useChat";
import { useFloatingPanel } from "@/lib/useFloatingPanel";
import { Icon } from "./Icon";
import { AgentPicker } from "./AgentPicker";
import { ChatAttachButton } from "./ChatAttachButton";
import { ChatMessageActions } from "./ChatMessageActions";
import { ChatMessageContent } from "./ChatMessageContent";
import { ThinkingDots } from "./ThinkingDots";

/** A floating, always-available chat popup (bottom-right on every page).
 *  Movable by its header and resizable from its bottom-right corner (see
 *  lib/useFloatingPanel, shared with the assignment detail modal's own
 *  draggable/resizable mode) so it can be dragged out of the way of
 *  whatever it's covering instead of being stuck in the corner. */
export function FloatingChat() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const {
    agent, setAgent, messages, busy, send, reset, confirmAction, dismissAction,
    attachment, setAttachment, regenerate, setFeedback,
  } = useChat();
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useAutoResizeTextarea(input);
  const { rect, startDrag, startResize } = useFloatingPanel({
    open, defaultWidth: 368, defaultHeight: 480, minWidth: 280, minHeight: 320,
    anchor: "bottom-right", marginX: 24, marginY: 96,
  });

  useEffect(() => {
    if (open) setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, [messages, open]);

  function submit() {
    const t = input.trim();
    if (!t) return;
    setInput("");
    send(t);
  }

  return (
    <>
      {open && rect && (
        <div
          className="fixed z-40 flex flex-col rounded-2xl border border-atlas-border bg-atlas-panel shadow-soft animate-fade-in"
          style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
        >
          <div
            className="flex items-center justify-between gap-2 p-3 border-b border-atlas-border cursor-move select-none"
            onPointerDown={startDrag}
          >
            <AgentPicker agent={agent} onChange={setAgent} />
            <div className="flex items-center gap-1">
              <button onClick={reset} title="New chat" aria-label="New chat"
                className="text-atlas-muted hover:text-atlas-text p-1.5 rounded-md hover:bg-atlas-panel2">
                <Icon name="plus" className="w-4 h-4" />
              </button>
              <Link href="/search" title="Open full chat" aria-label="Open full chat"
                className="text-atlas-muted hover:text-atlas-text p-1.5 rounded-md hover:bg-atlas-panel2">
                <Icon name="expand" className="w-4 h-4" />
              </Link>
              <button onClick={() => setOpen(false)} title="Close" aria-label="Close chat"
                className="text-atlas-muted hover:text-atlas-text p-1.5 rounded-md hover:bg-atlas-panel2">
                <Icon name="close" className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-auto p-3 space-y-3">
            {!messages.length && (
              <div className="text-xs text-atlas-muted text-center py-10">
                Ask Atlas anything — grounded in your courses, grades, and documents.
              </div>
            )}
            {messages.map((m, i) => {
              const isLastAssistant = m.role === "assistant" && i === messages.length - 1 && !busy;
              return (
                <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                  {m.role === "user" ? (
                    <div className="max-w-[85%] rounded-2xl px-3 py-2 text-sm bg-atlas-accent text-white">
                      {m.attachmentName && (
                        <div className="text-xs text-white/80 mb-1 flex items-center gap-1">
                          <Icon name="paperclip" className="w-3 h-3 shrink-0" /> {m.attachmentName}
                        </div>
                      )}
                      {m.content}
                    </div>
                  ) : (
                    <div>
                      <ChatMessageContent content={m.content} className="text-sm leading-relaxed" />
                      {m.pendingAction && (
                        <div className="flex gap-2 mt-1.5">
                          <button className="btn-primary !py-1 !px-2.5 text-xs" onClick={() => confirmAction(i)}>
                            Confirm
                          </button>
                          <button className="btn-ghost !py-1 !px-2.5 text-xs" onClick={() => dismissAction(i)}>
                            Cancel
                          </button>
                        </div>
                      )}
                      {!m.pendingAction && (
                        <ChatMessageActions
                          content={m.content}
                          feedback={m.feedback}
                          onFeedback={m.id ? (rating) => setFeedback(i, rating) : undefined}
                          onRegenerate={isLastAssistant ? regenerate : undefined}
                        />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {busy && <ThinkingDots label="Thinking" />}
            <div ref={endRef} />
          </div>

          <div className="p-2.5 border-t border-atlas-border flex items-end gap-2 flex-wrap">
            <ChatAttachButton attachment={attachment} onAttach={setAttachment} onClear={() => setAttachment(null)} />
            <textarea
              ref={textareaRef}
              className="input !py-1.5 flex-1 resize-none max-h-28 overflow-y-auto"
              placeholder="Message Atlas…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
              }}
              rows={1}
            />
            <button className="btn-primary !px-3 !py-1.5" onClick={submit} disabled={busy}>Send</button>
          </div>

          <div
            className="absolute bottom-0.5 right-0.5 w-4 h-4 cursor-nwse-resize text-atlas-muted"
            onPointerDown={(e) => {
              e.stopPropagation();
              startResize(e);
            }}
          >
            <Icon name="expand" className="w-full h-full" />
          </div>
        </div>
      )}

      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close Atlas chat" : "Ask Atlas"}
        className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-full bg-atlas-accent text-white grid place-items-center shadow-glow hover:brightness-110 transition-all"
      >
        <Icon name={open ? "close" : "message"} className="w-6 h-6" />
      </button>
    </>
  );
}
