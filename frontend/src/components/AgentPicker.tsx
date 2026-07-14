"use client";

import { useEffect, useRef, useState } from "react";
import { AGENTS } from "@/lib/useChat";

/** A Claude-model-selector-style dropdown for choosing which agent answers. */
export function AgentPicker({
  agent,
  onChange,
  up = false,
}: {
  agent: string;
  onChange: (id: string) => void;
  up?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = AGENTS.find((a) => a.id === agent) ?? AGENTS[0];

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-atlas-border bg-atlas-panel2 px-2.5 py-1.5 text-xs font-medium hover:border-atlas-accent/50 transition-colors"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-atlas-accent" />
        {current.label}
        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 opacity-70" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d={up ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} />
        </svg>
      </button>
      {open && (
        <div
          className={`absolute z-50 ${up ? "bottom-full mb-2" : "top-full mt-2"} left-0 w-60 rounded-xl border border-atlas-border bg-atlas-panel shadow-soft p-1 animate-fade-in`}
        >
          {AGENTS.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => { onChange(a.id); setOpen(false); }}
              className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                a.id === agent ? "bg-atlas-accent/10" : "hover:bg-atlas-panel2"
              }`}
            >
              <div className="text-sm font-medium flex items-center gap-2">
                {a.label}
                {a.id === agent && <span className="text-atlas-accent text-xs">✓</span>}
              </div>
              <div className="text-[11px] text-atlas-muted">{a.blurb}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
