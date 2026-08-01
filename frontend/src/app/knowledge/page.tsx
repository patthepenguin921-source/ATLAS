"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ChatMessageContent } from "@/components/ChatMessageContent";
import { DailyPlanCard } from "@/components/DailyPlanCard";
import { Empty, Badge, Section, SkeletonGrid, SkeletonList, gradeTone } from "@/components/ui";
import { apiGet, apiPost, apiPut } from "@/lib/api";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Color-codes the spaced-repetition quality buttons so "Again" reads as an
 *  alarm and "Easy" as a green light, instead of four identical gray
 *  buttons a student has to read to tell apart. */
const REVIEW_QUALITY_TONE: Record<string, string> = {
  bad: "text-atlas-bad hover:border-atlas-bad/50",
  warn: "text-atlas-warn hover:border-atlas-warn/50",
  good: "text-atlas-good hover:border-atlas-good/50",
  default: "",
};
const TABS = [
  { id: "today", label: "Today" },
  { id: "practice", label: "Practice" },
  { id: "flashcards", label: "Flashcards" },
  { id: "review", label: "Review & mastery" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function TabBar({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5 mb-6 border-b border-atlas-border">
      {TABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`px-4 py-2.5 -mb-px text-sm font-medium border-b-2 transition-colors ${
            tab === t.id
              ? "border-atlas-accent text-atlas-text"
              : "border-transparent text-atlas-muted hover:text-atlas-text hover:border-atlas-border"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Weekly study-availability editor -- how many minutes the student
 *  actually has each day, since that varies (practice/work some days,
 *  nothing others). Read by the Planner instead of a flat guess. */
function AvailabilityEditor() {
  const [minutes, setMinutes] = useState<number[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      const a = await apiGet("/study-availability");
      setMinutes(Array.from({ length: 7 }, (_, d) => Number(a?.[d] ?? 60)));
      setLoadError(null);
    } catch (e: any) {
      setLoadError(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  function setDay(day: number, value: number) {
    setMinutes((prev) => (prev ? prev.map((m, i) => (i === day ? Math.max(0, value) : m)) : prev));
    setSaved(false);
  }

  async function save() {
    if (!minutes) return;
    setSaving(true);
    try {
      const schedule: Record<number, number> = {};
      minutes.forEach((m, d) => (schedule[d] = m));
      await apiPut("/study-availability", { schedule });
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  if (loadError && !minutes) {
    return (
      <div className="card border-atlas-bad/40 text-sm">
        <div className="font-medium text-atlas-bad">Couldn't load your schedule</div>
        <div className="text-atlas-muted mt-1">{loadError}</div>
        <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
      </div>
    );
  }
  if (!minutes) return <SkeletonList rows={1} />;

  return (
    <div className="card">
      <p className="text-sm text-atlas-muted mb-3">
        How many minutes can you actually study each day? Atlas's daily plan will fit itself to
        this instead of assuming the same amount of free time every day.
      </p>
      <div className="grid grid-cols-4 sm:grid-cols-7 gap-2">
        {DAY_NAMES.map((name, d) => (
          <div key={d} className="flex flex-col items-center gap-1">
            <label className="text-xs text-atlas-muted">{name}</label>
            <input
              type="number"
              min={0}
              step={5}
              className="input !w-full !py-1.5 text-center text-sm"
              value={minutes[d]}
              onChange={(e) => setDay(d, parseInt(e.target.value, 10) || 0)}
            />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 mt-3">
        <button className="btn-primary text-sm" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save schedule"}
        </button>
        {saved && <span className="text-xs text-atlas-good">Saved.</span>}
      </div>
    </div>
  );
}

function TodayTab() {
  const [plan, setPlan] = useState<any>(null);
  const [planning, setPlanning] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function loadPlan() {
    try {
      const rows = await apiGet("/reviews/plans");
      const today = new Date().toISOString().slice(0, 10);
      setPlan(rows?.find((p: any) => p.plan_date === today) ?? null);
    } finally {
      setLoaded(true);
    }
  }
  useEffect(() => {
    loadPlan();
  }, []);

  async function generate() {
    setPlanning(true);
    try {
      // No available_minutes -- the backend reads today's real number from
      // the schedule below instead of a flat guess.
      await apiPost("/agents/planner/daily-plan", {});
      await loadPlan();
    } finally {
      setPlanning(false);
    }
  }

  return (
    <>
      <Section title="Your study time">
        <AvailabilityEditor />
      </Section>
      <Section
        title="Today's plan"
        action={
          <button className="btn-primary text-sm" onClick={generate} disabled={planning}>
            {planning ? "Planning…" : plan ? "Regenerate" : "Generate today's plan"}
          </button>
        }
      >
        {!loaded && <SkeletonList rows={2} />}
        {loaded && !plan && (
          <Empty>No plan for today yet -- generate one above, grounded in your real assignments and the time you set.</Empty>
        )}
        {plan && <DailyPlanCard plan={plan} />}
      </Section>
    </>
  );
}

function PracticeTab({ courses }: { courses: any[] }) {
  const [courseId, setCourseId] = useState("");
  const [folders, setFolders] = useState<any[]>([]);
  const [folderId, setFolderId] = useState("");
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<"quiz" | "test">("quiz");
  const [count, setCount] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());

  const [explainTopic, setExplainTopic] = useState("");
  const [explainDepth, setExplainDepth] = useState<"quick" | "standard" | "deep">("standard");
  const [explaining, setExplaining] = useState(false);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [explainError, setExplainError] = useState<string | null>(null);

  useEffect(() => {
    setFolderId("");
    if (!courseId) {
      setFolders([]);
      return;
    }
    apiGet(`/folders?course_id=${courseId}`).then((f) => setFolders(f ?? []));
  }, [courseId]);

  async function generate() {
    if (!topic.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setRevealed(new Set());
    try {
      const r = await apiPost("/agents/tutor/quiz", {
        topic: topic.trim(),
        num_questions: count,
        mode,
        course_id: courseId || undefined,
        folder_id: folderId || undefined,
      });
      setResult(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function toggleReveal(i: number) {
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  async function explain() {
    if (!explainTopic.trim()) return;
    setExplaining(true);
    setExplanation(null);
    setExplainError(null);
    try {
      const r = await apiPost("/agents/tutor/explain", {
        topic: explainTopic.trim(),
        depth: explainDepth,
        course_id: courseId || undefined,
        folder_id: folderId || undefined,
      });
      setExplanation(r.explanation);
    } catch (e: any) {
      setExplainError(e.message);
    } finally {
      setExplaining(false);
    }
  }

  return (
    <>
      <Section title="Get a tailored practice quiz or test">
        <div className="card">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Class</label>
              <select className="input" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                <option value="">Any class</option>
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Unit (optional)</label>
              <select
                className="input"
                value={folderId}
                onChange={(e) => setFolderId(e.target.value)}
                disabled={!courseId || !folders.length}
              >
                <option value="">{courseId ? "Whole class" : "Pick a class first"}</option>
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-3">
            <label className="label">Topic</label>
            <input
              className="input"
              placeholder="e.g. cell division, Newton's laws, chapter 5 vocab…"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && generate()}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3 mt-3">
            <div className="flex gap-1.5">
              <button
                className={`pill ${mode === "quiz" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
                onClick={() => setMode("quiz")}
              >
                Quick quiz
              </button>
              <button
                className={`pill ${mode === "test" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
                onClick={() => setMode("test")}
              >
                Practice test
              </button>
            </div>
            <label className="text-xs text-atlas-muted flex items-center gap-1.5">
              Questions
              <input
                type="number" min={1} max={15}
                className="input !w-16 !py-1 text-center text-sm"
                value={count}
                onChange={(e) => setCount(Math.max(1, Math.min(15, parseInt(e.target.value, 10) || 1)))}
              />
            </label>
            <button className="btn-primary text-sm ml-auto" onClick={generate} disabled={loading || !topic.trim()}>
              {loading ? "Generating…" : "Generate"}
            </button>
          </div>
        </div>

        {error && <div className="card border-atlas-bad/40 text-atlas-bad text-sm mt-3">{error}</div>}

        {result?.questions?.length > 0 && (
          <div className="space-y-3 mt-4">
            {result.questions.map((q: any, i: number) => (
              <div key={i} className="card">
                <div className="flex items-start justify-between gap-3">
                  <div className="text-sm font-medium">{i + 1}. {q.q}</div>
                  {q.concept && <Badge tone="accent">{q.concept}</Badge>}
                </div>
                {q.choices?.length > 0 && (
                  <ul className="text-sm text-atlas-muted mt-2 space-y-1 list-disc list-inside">
                    {q.choices.map((c: string, ci: number) => <li key={ci}>{c}</li>)}
                  </ul>
                )}
                {revealed.has(i) ? (
                  <div className="mt-2 text-sm border-t border-atlas-border pt-2">
                    <div><span className="text-atlas-good font-medium">Answer:</span> {q.answer}</div>
                    {q.explanation && <div className="text-atlas-muted mt-1">{q.explanation}</div>}
                  </div>
                ) : (
                  <button className="btn-ghost text-xs mt-2" onClick={() => toggleReveal(i)}>Reveal answer</button>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Or just explain something">
        <div className="card">
          <div className="flex flex-wrap gap-2">
            <input
              className="input flex-1 min-w-[12rem]"
              placeholder="What should Atlas explain? (uses the class/unit above, if set)"
              value={explainTopic}
              onChange={(e) => setExplainTopic(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && explain()}
            />
            <div className="flex gap-1">
              {(["quick", "standard", "deep"] as const).map((d) => (
                <button
                  key={d}
                  className={`pill capitalize ${explainDepth === d ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
                  onClick={() => setExplainDepth(d)}
                >
                  {d}
                </button>
              ))}
            </div>
            <button className="btn-primary text-sm" onClick={explain} disabled={explaining || !explainTopic.trim()}>
              {explaining ? "Thinking…" : "Explain"}
            </button>
          </div>
          {explainError && (
            <div className="mt-3 border-t border-atlas-border pt-3 text-sm text-atlas-bad">{explainError}</div>
          )}
          {explanation && (
            <div className="mt-3 border-t border-atlas-border pt-3">
              <ChatMessageContent content={explanation} className="text-sm leading-relaxed" />
            </div>
          )}
        </div>
      </Section>
    </>
  );
}

function FlashcardsTab({ courses }: { courses: any[] }) {
  const [courseId, setCourseId] = useState("");
  const [folders, setFolders] = useState<any[]>([]);
  const [folderId, setFolderId] = useState("");
  const [docs, setDocs] = useState<any[]>([]);
  const [docId, setDocId] = useState("");
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const [deck, setDeck] = useState<any[] | null>(null);
  const [deckError, setDeckError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);

  async function loadDeck() {
    try {
      const q = await apiGet("/flashcards/review-queue");
      setDeck(q ?? []);
      setDeckError(null);
    } catch (e: any) {
      setDeckError(e.message);
      return;
    }
    setIndex(0);
    setFlipped(false);
  }
  useEffect(() => {
    loadDeck();
  }, []);

  useEffect(() => {
    setFolderId("");
    setDocId("");
    if (!courseId) {
      setFolders([]);
      setDocs([]);
      return;
    }
    apiGet(`/folders?course_id=${courseId}`).then((f) => setFolders(f ?? []));
  }, [courseId]);

  useEffect(() => {
    setDocId("");
    if (!courseId) {
      setDocs([]);
      return;
    }
    const q = new URLSearchParams({ course_id: courseId });
    if (folderId) q.set("folder_id", folderId);
    apiGet(`/documents?${q.toString()}`).then((d) => setDocs(d ?? []));
  }, [courseId, folderId]);

  async function generate() {
    setGenerating(true);
    setMessage(null);
    try {
      const r = await apiPost("/flashcards/generate", {
        document_id: docId || undefined,
        course_id: docId ? undefined : (courseId || undefined),
        folder_id: docId ? undefined : (folderId || undefined),
        max_cards: 15,
      });
      setMessage({ ok: true, text: `Generated ${r.count} flashcards from "${r.source_label}".` });
      loadDeck();
    } catch (e: any) {
      setMessage({ ok: false, text: e.message });
    } finally {
      setGenerating(false);
    }
  }

  async function reviewCard(quality: number) {
    if (!deck?.length) return;
    const card = deck[index];
    await apiPost(`/flashcards/${card.id}/review`, { quality });
    const next = deck.filter((_, i) => i !== index);
    setDeck(next);
    setFlipped(false);
    setIndex((i) => Math.min(i, Math.max(0, next.length - 1)));
  }

  const scopeReady = docId || folderId || courseId;
  const current = deck?.[index];

  return (
    <>
      <Section title="Generate flashcards">
        <div className="card">
          <p className="text-sm text-atlas-muted mb-3">
            Pick a class, then optionally a unit or one specific document -- Atlas gathers the key
            terms and turns them into cards for spaced-repetition review below.
          </p>
          <div className="grid sm:grid-cols-3 gap-3">
            <div>
              <label className="label">Class</label>
              <select className="input" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                <option value="">Pick a class…</option>
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Unit (optional)</label>
              <select
                className="input" value={folderId} onChange={(e) => setFolderId(e.target.value)}
                disabled={!courseId || !folders.length}
              >
                <option value="">Whole class</option>
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Or one document</label>
              <select
                className="input" value={docId} onChange={(e) => setDocId(e.target.value)}
                disabled={!docs.length}
              >
                <option value="">{docs.length ? "Any document in scope" : "No documents here"}</option>
                {docs.map((d) => (
                  <option key={d.id} value={d.id}>{d.title}</option>
                ))}
              </select>
            </div>
          </div>
          <button className="btn-primary text-sm mt-3" onClick={generate} disabled={generating || !scopeReady}>
            {generating ? "Generating…" : "Generate flashcards"}
          </button>
          {message && (
            <div className={`text-xs mt-2 ${message.ok ? "text-atlas-good" : "text-atlas-bad"}`}>
              {message.text}
            </div>
          )}
        </div>
      </Section>

      <Section title={`Review${deck ? ` (${deck.length} due)` : ""}`}>
        {!deck && !deckError && <SkeletonList rows={1} />}
        {deckError && !deck && (
          <div className="card border-atlas-bad/40 text-sm">
            <div className="font-medium text-atlas-bad">Couldn't load your review deck</div>
            <div className="text-atlas-muted mt-1">{deckError}</div>
            <button className="btn-ghost text-xs mt-2" onClick={() => loadDeck()}>Retry</button>
          </div>
        )}
        {deck && !deck.length && <Empty>Nothing due for review right now.</Empty>}
        {current && (
          <div className="card">
            <button
              type="button"
              className="w-full min-h-[8rem] flex items-center justify-center text-center px-4 py-6 rounded-xl border border-atlas-border bg-atlas-panel2 text-base"
              onClick={() => setFlipped((f) => !f)}
            >
              {flipped ? current.back : current.front}
            </button>
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs text-atlas-muted">
                {current.source_label ? `From: ${current.source_label}` : null}
              </span>
              <span className="text-xs text-atlas-muted">{index + 1} / {deck.length}</span>
            </div>
            {!flipped ? (
              <button className="btn-ghost text-xs mt-2" onClick={() => setFlipped(true)}>Show answer</button>
            ) : (
              <div className="flex gap-1 mt-2">
                {[
                  { q: 1, label: "Again", tone: "bad" },
                  { q: 3, label: "Hard", tone: "warn" },
                  { q: 4, label: "Good", tone: "default" },
                  { q: 5, label: "Easy", tone: "good" },
                ].map((b) => (
                  <button
                    key={b.q}
                    className={`btn-ghost text-xs py-1 ${REVIEW_QUALITY_TONE[b.tone]}`}
                    onClick={() => reviewCard(b.q)}
                  >
                    {b.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </Section>
    </>
  );
}

function ReviewMasteryTab() {
  const [model, setModel] = useState<any[] | null>(null);
  const [queue, setQueue] = useState<any[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function load() {
    try {
      const [m, q] = await Promise.all([
        apiGet("/knowledge/model"),
        apiGet("/knowledge/review-queue"),
      ]);
      setModel(m);
      setQueue(q);
      setLoadError(null);
    } catch (e: any) {
      setLoadError(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function review(conceptId: string, quality: number) {
    await apiPost("/knowledge/review", { concept_id: conceptId, quality });
    load();
  }

  return (
    <>
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
                    <button
                      key={b.q}
                      className={`btn-ghost text-xs py-1 ${REVIEW_QUALITY_TONE[b.tone]}`}
                      onClick={() => review(r.concept_id, b.q)}
                    >
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
        {!model && !loadError && <SkeletonGrid items={4} />}
        {loadError && !model && (
          <div className="card border-atlas-bad/40 text-sm">
            <div className="font-medium text-atlas-bad">Couldn't load your knowledge model</div>
            <div className="text-atlas-muted mt-1">{loadError}</div>
            <button className="btn-ghost text-xs mt-2" onClick={() => load()}>Retry</button>
          </div>
        )}
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
    </>
  );
}

export default function KnowledgePage() {
  const [tab, setTab] = useState<Tab>("today");
  const [courses, setCourses] = useState<any[]>([]);

  useEffect(() => {
    apiGet("/courses").then((c) => setCourses((c ?? []).filter((x: any) => x.is_active !== false)));
  }, []);

  return (
    <AppShell title="Study">
      <TabBar tab={tab} onChange={setTab} />
      {tab === "today" && <TodayTab />}
      {tab === "practice" && <PracticeTab courses={courses} />}
      {tab === "flashcards" && <FlashcardsTab courses={courses} />}
      {tab === "review" && <ReviewMasteryTab />}
    </AppShell>
  );
}
