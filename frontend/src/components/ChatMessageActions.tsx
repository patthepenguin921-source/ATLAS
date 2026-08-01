"use client";

import { useState } from "react";
import { Icon } from "./Icon";

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

  const btn = "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs hover:bg-atlas-panel2 hover:text-atlas-text transition-colors";

  return (
    <div className="flex items-center gap-0.5 mt-1 -ml-1.5 text-atlas-muted">
      <button onClick={copy} title="Copy" aria-label="Copy response" className={btn}>
        {copied ? (
          <>
            <Icon name="check" className="w-3.5 h-3.5" /> Copied
          </>
        ) : (
          <Icon name="copy" className="w-3.5 h-3.5" />
        )}
      </button>
      {onFeedback && (
        <>
          <button
            onClick={() => onFeedback("up")}
            title="Good response"
            aria-label="Good response"
            aria-pressed={feedback === "up"}
            className={`${btn} ${feedback === "up" ? "text-atlas-accent" : ""}`}
          >
            <Icon name="thumbsUp" className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onFeedback("down")}
            title="Not helpful"
            aria-label="Not helpful"
            aria-pressed={feedback === "down"}
            className={`${btn} ${feedback === "down" ? "text-atlas-bad" : ""}`}
          >
            <Icon name="thumbsDown" className="w-3.5 h-3.5" />
          </button>
        </>
      )}
      {onRegenerate && (
        <button onClick={onRegenerate} title="Regenerate response" className={btn}>
          <Icon name="refresh" className="w-3.5 h-3.5" /> Regenerate
        </button>
      )}
    </div>
  );
}
