"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, Section, gradeTone } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";

export default function KnowledgePage() {
  const [model, setModel] = useState<any[] | null>(null);
  const [queue, setQueue] = useState<any[]>([]);

  async function load() {
    const [m, q] = await Promise.all([
      apiGet("/knowledge/model"),
      apiGet("/knowledge/review-queue"),
    ]);
    setModel(m);
    setQueue(q);
  }
  useEffect(() => {
    load();
  }, []);

  async function review(conceptId: string, quality: number) {
    await apiPost("/knowledge/review", { concept_id: conceptId, quality });
    load();
  }

  return (
    <AppShell title="Knowledge" subtitle="Atlas's model of what you understand">
      <Section title="Review now (spaced repetition)">
        {!queue.length ? (
          <Empty>Nothing due for review. Your memory is fresh.</Empty>
        ) : (
          <div className="space-y-2">
            {queue.map((r) => (
              <div key={r.concept_id} className="card flex items-center justify-between gap-4">
                <div>
                  <div className="font-medium">{r.name ?? "Concept"}</div>
                  <div className="text-xs text-atlas-muted">
                    retention {(r.retention ?? 0).toFixed?.(2)} · mastery {(r.mastery ?? 0).toFixed?.(2)}
                  </div>
                </div>
                <div className="flex gap-1">
                  {[
                    { q: 1, label: "Again", tone: "bad" },
                    { q: 3, label: "Hard", tone: "warn" },
                    { q: 4, label: "Good", tone: "default" },
                    { q: 5, label: "Easy", tone: "good" },
                  ].map((b) => (
                    <button key={b.q} className="btn-ghost text-xs py-1"
                      onClick={() => review(r.concept_id, b.q)}>
                      {b.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Concept mastery">
        {!model && <Loading />}
        {model && !model.length && <Empty>No concepts tracked yet. Upload documents to build your map.</Empty>}
        <div className="grid md:grid-cols-2 gap-3">
          {model?.map((m) => (
            <div key={m.concept_id} className="card">
              <div className="flex items-center justify-between">
                <div className="font-medium text-sm">{m.concept_name ?? "Concept"}</div>
                <Badge tone={gradeTone((m.retention ?? 0) * 100)}>
                  {(m.retention ?? 0).toFixed?.(2)} ret
                </Badge>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-atlas-panel2 overflow-hidden">
                <div className="h-full bg-atlas-accent" style={{ width: `${(m.mastery ?? 0) * 100}%` }} />
              </div>
              <div className="text-xs text-atlas-muted mt-1">
                mastery {(m.mastery ?? 0).toFixed?.(2)} · {m.evidence_count ?? 0} signals
              </div>
            </div>
          ))}
        </div>
      </Section>
    </AppShell>
  );
}
