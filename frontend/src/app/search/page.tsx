"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Badge } from "@/components/ui";
import { apiPost } from "@/lib/api";

const EXAMPLES = [
  "What mistakes do I keep making in AP Calculus?",
  "Show every assignment related to photosynthesis.",
  "What did I learn before Biology Quiz 3?",
  "What feedback has my English teacher repeated this year?",
];

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  async function ask(query?: string) {
    const question = query ?? q;
    if (!question.trim()) return;
    setQ(question);
    setBusy(true);
    setErr(null);
    setRes(null);
    try {
      setRes(await apiPost("/search/ask", { query: question, limit: 8 }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Search" subtitle="Ask anything about your academic life">
      <div className="card mb-6">
        <div className="flex gap-2">
          <input
            className="input"
            placeholder="Ask Atlas…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
          />
          <button className="btn-primary" onClick={() => ask()} disabled={busy}>
            {busy ? "Thinking…" : "Ask"}
          </button>
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="pill text-atlas-muted hover:text-atlas-text"
              onClick={() => ask(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </div>

      {err && <div className="card border-atlas-bad/40 text-atlas-bad text-sm">{err}</div>}

      {res && (
        <div className="space-y-4">
          <div className="card">
            <div className="text-xs text-atlas-muted mb-2">Answer</div>
            <div className="prose prose-invert text-sm whitespace-pre-wrap">{res.answer}</div>
          </div>
          {res.sources?.length ? (
            <div className="card">
              <div className="text-xs text-atlas-muted mb-2">Sources</div>
              <div className="flex flex-wrap gap-2">
                {res.sources.map((s: any, i: number) => (
                  <Badge key={i} tone="accent">
                    {s.document_title} {s.similarity ? `· ${(s.similarity * 100).toFixed(0)}%` : ""}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </AppShell>
  );
}
