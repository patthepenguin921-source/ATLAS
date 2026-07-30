"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useChat } from "@/lib/useChat";
import { AgentPicker } from "./AgentPicker";
import { ChatAttachButton } from "./ChatAttachButton";

/** A floating, always-available chat popup (bottom-right on every page). */
export function FloatingChat() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const {
    agent, setAgent, messages, busy, send, reset, confirmAction, dismissAction,
    attachment, setAttachment,
  } = useChat();
  const endRef = useRef<HTMLDivElement>(null);

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
      {open && (
        <div className="fixed bottom-24 right-6 z-40 w-[min(23rem,calc(100vw-3rem))] h-[30rem] max-h-[70vh] flex flex-col rounded-2xl border border-atlas-border bg-atlas-panel shadow-soft animate-fade-in">
          <div className="flex items-center justify-between gap-2 p-3 border-b border-atlas-border">
            <AgentPicker agent={agent} onChange={setAgent} />
            <div className="flex items-center gap-1">
              <button onClick={reset} title="New chat"
                className="text-atlas-muted hover:text-atlas-text text-sm px-1.5">＋</button>
              <Link href="/search" title="Open full chat"
                className="text-atlas-muted hover:text-atlas-text text-sm px-1.5">⤢</Link>
              <button onClick={() => setOpen(false)} title="Close"
                className="text-atlas-muted hover:text-atlas-text text-lg leading-none px-1.5">×</button>
            </div>
          </div>

          <div className="flex-1 overflow-auto p-3 space-y-3">
            {!messages.length && (
              <div className="text-xs text-atlas-muted text-center py-10">
                Ask Atlas anything — grounded in your courses, grades, and documents.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                {m.role === "user" ? (
                  <div className="max-w-[85%] rounded-2xl px-3 py-2 text-sm bg-atlas-accent text-white">
                    {m.attachmentName && (
                      <div className="text-xs text-white/80 mb-1">📎 {m.attachmentName}</div>
                    )}
                    {m.content}
                  </div>
                ) : (
                  <div>
                    <div className="text-sm whitespace-pre-wrap leading-relaxed">{m.content}</div>
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
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="text-xs text-atlas-muted">Thinking…</div>}
            <div ref={endRef} />
          </div>

          <div className="p-2.5 border-t border-atlas-border flex items-center gap-2 flex-wrap">
            <ChatAttachButton attachment={attachment} onAttach={setAttachment} onClear={() => setAttachment(null)} />
            <input
              className="input !py-1.5"
              placeholder="Message Atlas…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
            <button className="btn-primary !px-3 !py-1.5" onClick={submit} disabled={busy}>Send</button>
          </div>
        </div>
      )}

      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Ask Atlas"
        className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-full bg-atlas-accent text-white grid place-items-center shadow-glow hover:brightness-110 transition-all text-2xl"
      >
        {open ? "×" : "✦"}
      </button>
    </>
  );
}
