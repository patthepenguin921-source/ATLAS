"use client";

import { useState } from "react";

/** The small copy/feedback/regenerate row under one of Atlas's replies.
 *  `onFeedback`/`showRegenerate` are both optional so the same row works in
 *  the floating popup (copy only, kept minimal) and the full Ask Atlas page
 *  (copy + feedback + regenerate on the latest reply). */
export function ChatMessageActions({
  content,
  feedback,
  onFeedback,
  onRegenerate,
}: {
  content: string;
  feedback?: "up" | "down" | null;
  onFeedback?: (rating: "up" | "down") => void;
  onRegenerate?: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (e.g. insecure context) -- fail silently */
    }
  }

  const btn = "rounded-md px-1.5 py-1 text-xs hover:bg-atlas-panel2 hover:text-atlas-text transition-colors";

  return (
    <div className="flex items-center gap-0.5 mt-1 -ml-1.5 text-atlas-muted">
      <button onClick={copy} title="Copy" className={btn}>
        {copied ? "Copied ✓" : "⧉"}
      </button>
      {onFeedback && (
        <>
          <button
            onClick={() => onFeedback("up")}
            title="Good response"
            className={`${btn} ${feedback === "up" ? "text-atlas-accent" : ""}`}
          >
            👍
          </button>
          <button
            onClick={() => onFeedback("down")}
            title="Not helpful"
            className={`${btn} ${feedback === "down" ? "text-atlas-bad" : ""}`}
          >
            👎
          </button>
        </>
      )}
      {onRegenerate && (
        <button onClick={onRegenerate} title="Regenerate response" className={btn}>
          ↻ Regenerate
        </button>
      )}
    </div>
  );
}
