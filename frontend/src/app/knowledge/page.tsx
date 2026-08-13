"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ChatMessageContent } from "@/components/ChatMessageContent";
import { DailyPlanCard } from "@/components/DailyPlanCard";
import { Empty, Badge, Section, SkeletonGrid, SkeletonList, gradeTone } from "@/components/ui";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { groupCourses, currentMember } from "@/lib/courseGroups";

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

function PracticeTab({ courses }: { courses: { id: string; name: string }[] }) {
  const [courseId, setCourseId] = useState("");
  const [folders, setFolders] = useState<any[]>([]);
  const [folderId, setFolderId] = useState("");
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<"quiz" | "test">("quiz");
  const [source, setSource] = useState<"materials" | "internet">("materials");
  const [count, setCount] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  // Answers for the freshly-generated quiz above, and the grading result
  // once submitted (see POST /practice/{id}/submit -- app.services.mistake_analysis).
  // Grading records wrong answers as mistakes and updates the concept
  // knowledge model, which plain "reveal answer" never did.
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [grading, setGrading] = useState(false);
  const [graded, setGraded] = useState<any>(null);
  const [gradeError, setGradeError] = useState<string | null>(null);

  const [history, setHistory] = useState<any[] | null>(null);
  const [viewingPast, setViewingPast] = useState<any | null>(null);
  const [pastRevealed, setPastRevealed] = useState<Set<number>>(new Set());
  const [pastError, setPastError] = useState<string | null>(null);

  async function loadHistory() {
    try {
      setHistory(await apiGet("/practice"));
    } catch {
      setHistory([]);
    }
  }
  useEffect(() => {
    loadHistory();
  }, []);

  async function openPast(id: string) {
    setPastError(null);
    setPastRevealed(new Set());
    try {
      setViewingPast(await apiGet(`/practice/${id}`));
    } catch (e: any) {
      setPastError(e.message);
    }
  }

  async function deletePast(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await apiDelete(`/practice/${id}`);
    if (viewingPast?.id === id) setViewingPast(null);
    loadHistory();
  }

  const courseName = (id: string | null) => courses.find((c) => c.id === id)?.name;

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
    setAnswers({});
    setGraded(null);
    setGradeError(null);
    try {
      const r = await apiPost("/agents/tutor/quiz", {
        topic: topic.trim(),
        num_questions: count,
        mode,
        source,
        course_id: courseId || undefined,
        folder_id: folderId || undefined,
      });
      setResult(r);
      loadHistory();
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

  async function submitForGrading() {
    if (!result?.practice_session_id) return;
    setGrading(true);
    setGradeError(null);
    try {
      const answerList = result.questions.map((_: any, i: number) => answers[i] ?? null);
      const r = await apiPost(`/practice/${result.practice_session_id}/submit`, { answers: answerList });
      setGraded(r);
      loadHistory();
    } catch (e: any) {
      setGradeError(e.message);
    } finally {
      setGrading(false);
    }
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
            <div className="flex gap-1.5">
              <button
                className={`pill ${source === "materials" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
                onClick={() => setSource("materials")}
                title="Grounded in your own documents/notes"
              >
                My materials
              </button>
              <button
                className={`pill ${source === "internet" ? "border-atlas-accent/60 text-atlas-accent" : "text-atlas-muted"}`}
                onClick={() => setSource("internet")}
                title="Generated from a live web search instead of your own materials"
              >
                From the internet
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
            {result.source === "internet" && (
              <div className="text-xs text-atlas-warn">
                From the internet -- not grounded in your own materials.
              </div>
            )}
            {result.questions.map((q: any, i: number) => {
              const graded_i = graded?.results?.find((r: any) => r.i === i);
              return (
                <div key={i} className="card">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-sm font-medium">{i + 1}. {q.q}</div>
                    {q.concept && <Badge tone="accent">{q.concept}</Badge>}
                  </div>

                  {!graded && q.type === "multiple_choice" && q.choices?.length > 0 ? (
                    <div className="text-sm mt-2 space-y-1">
                      {q.choices.map((c: string, ci: number) => (
                        <label key={ci} className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name={`q-${i}`}
                            checked={answers[i] === c}
                            onChange={() => setAnswers((a) => ({ ...a, [i]: c }))}
                          />
                          {c}
                        </label>
                      ))}
                    </div>
                  ) : !graded ? (
                    <input
                      className="input mt-2"
                      placeholder="Your answer…"
                      value={answers[i] ?? ""}
                      onChange={(e) => setAnswers((a) => ({ ...a, [i]: e.target.value }))}
                    />
                  ) : q.choices?.length > 0 ? (
                    <ul className="text-sm text-atlas-muted mt-2 space-y-1 list-disc list-inside">
                      {q.choices.map((c: string, ci: number) => <li key={ci}>{c}</li>)}
                    </ul>
                  ) : null}

                  {graded_i ? (
                    <div className="mt-2 text-sm border-t border-atlas-border pt-2">
                      <div className="flex items-center gap-2">
                        <Badge tone={graded_i.correct ? "good" : "bad"}>
                          {graded_i.correct ? "Correct" : "Incorrect"}
                        </Badge>
                        {graded_i.mistake_type && <Badge tone="warn">{graded_i.mistake_type}</Badge>}
                      </div>
                      {graded_i.your_answer && (
                        <div className="text-atlas-muted mt-1">You answered: {graded_i.your_answer}</div>
                      )}
                      {!graded_i.correct && (
                        <div><span className="text-atlas-good font-medium">Answer:</span> {q.answer}</div>
                      )}
                      {(graded_i.correction || q.explanation) && (
                        <div className="text-atlas-muted mt-1">{graded_i.correction || q.explanation}</div>
                      )}
                    </div>
                  ) : revealed.has(i) ? (
                    <div className="mt-2 text-sm border-t border-atlas-border pt-2">
                      <div><span className="text-atlas-good font-medium">Answer:</span> {q.answer}</div>
                      {q.explanation && <div className="text-atlas-muted mt-1">{q.explanation}</div>}
                    </div>
                  ) : (
                    <button className="btn-ghost text-xs mt-2" onClick={() => toggleReveal(i)}>Reveal answer</button>
                  )}
                </div>
              );
            })}
            {!graded && (
              <div className="flex items-center gap-3">
                <button
                  className="btn-primary text-sm"
                  onClick={submitForGrading}
                  disabled={grading || !result.practice_session_id}
                  title={!result.practice_session_id ? "This quiz wasn't saved, so it can't be graded" : undefined}
                >
                  {grading ? "Grading…" : "Submit for grading"}
                </button>
                <span className="text-xs text-atlas-muted">
                  Grading records what you got wrong so Atlas can review it with you later.
                </span>
              </div>
            )}
            {gradeError && <div className="text-sm text-atlas-bad">{gradeError}</div>}
            {graded && (
              <div className="card border-atlas-accent/40 flex items-center justify-between">
                <span className="text-sm font-medium">Score: {graded.score}%</span>
                <Badge tone={graded.score >= 80 ? "good" : graded.score >= 60 ? "warn" : "bad"}>
                  {graded.results.filter((r: any) => r.correct).length}/{graded.results.length} correct
                </Badge>
              </div>
            )}
          </div>
        )}
      </Section>

      <Section title="Past practice">
        {!history && <div className="text-sm text-atlas-muted">Loading…</div>}
        {history && !history.length && <Empty>No practice generated yet -- try the form above.</Empty>}
        {history && history.length > 0 && (
          <div className="space-y-2">
            {history.map((h) => (
              <div
                key={h.id}
                className="card card-hover cursor-pointer flex items-center justify-between gap-4"
                onClick={() => openPast(h.id)}
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{h.topic}</div>
                  <div className="text-xs text-atlas-muted">
                    {h.mode === "test" ? "Practice test" : "Quick quiz"}
                    {h.source === "internet" ? " · from the internet" : ""}
                    {courseName(h.course_id) ? ` · ${courseName(h.course_id)}` : ""}
                    {" · "}{new Date(h.created_at).toLocaleDateString()}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {h.score != null && <Badge tone="good">{h.score}%</Badge>}
                  <button
                    className="text-xs text-atlas-muted hover:text-atlas-bad"
                    onClick={(e) => deletePast(h.id, e)}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {viewingPast && (
          <div className="card mt-3">
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="text-sm font-medium">{viewingPast.topic}</div>
              <button className="text-xs text-atlas-muted hover:text-atlas-text" onClick={() => setViewingPast(null)}>
                Close
              </button>
            </div>
            {viewingPast.source === "internet" && (
              <div className="text-xs text-atlas-warn mb-2">From the internet -- not grounded in your own materials.</div>
            )}
            {pastError && <div className="text-sm text-atlas-bad">{pastError}</div>}
            <div className="space-y-3">
              {(viewingPast.questions ?? []).map((q: any, i: number) => (
                <div key={i} className="border-t border-atlas-border pt-2 first:border-t-0 first:pt-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-sm font-medium">{i + 1}. {q.q}</div>
                    {q.concept && <Badge tone="accent">{q.concept}</Badge>}
                  </div>
                  {q.choices?.length > 0 && (
                    <ul className="text-sm text-atlas-muted mt-2 space-y-1 list-disc list-inside">
                      {q.choices.map((c: string, ci: number) => <li key={ci}>{c}</li>)}
                    </ul>
                  )}
                  {pastRevealed.has(i) ? (
                    <div className="mt-2 text-sm">
                      <div><span className="text-atlas-good font-medium">Answer:</span> {q.answer}</div>
                      {q.explanation && <div className="text-atlas-muted mt-1">{q.explanation}</div>}
                    </div>
                  ) : (
                    <button
                      className="btn-ghost text-xs mt-2"
                      onClick={() => setPastRevealed((prev) => new Set(prev).add(i))}
                    >
                      Reveal answer
                    </button>
                  )}
                </div>
              ))}
            </div>
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

function FlashcardsTab({ courses }: { courses: { id: string; name: string }[] }) {
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

  // All flashcards ever generated, not just what's currently due -- a
  // separate browse/history view from the spaced-repetition queue below.
  const [allCards, setAllCards] = useState<any[] | null>(null);
  const [allCardsError, setAllCardsError] = useState<string | null>(null);
  const [expandedCard, setExpandedCard] = useState<string | null>(null);

  async function loadAllCards() {
    try {
      setAllCards(await apiGet("/flashcards"));
      setAllCardsError(null);
    } catch (e: any) {
      setAllCardsError(e.message);
    }
  }
  useEffect(() => {
    loadAllCards();
  }, []);

  async function deleteCard(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await apiDelete(`/flashcards/${id}`);
    loadAllCards();
    loadDeck();
  }

  const courseName = (id: string | null) => courses.find((c) => c.id === id)?.name;

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
      loadAllCards();
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

      <Section title={`Past flashcards${allCards ? ` (${allCards.length})` : ""}`}>
        {!allCards && !allCardsError && <SkeletonList rows={1} />}
        {allCardsError && !allCards && (
          <div className="card border-atlas-bad/40 text-sm">
            <div className="font-medium text-atlas-bad">Couldn't load your flashcards</div>
            <div className="text-atlas-muted mt-1">{allCardsError}</div>
            <button className="btn-ghost text-xs mt-2" onClick={() => loadAllCards()}>Retry</button>
          </div>
        )}
        {allCards && !allCards.length && <Empty>No flashcards generated yet.</Empty>}
        {allCards && allCards.length > 0 && (
          <div className="space-y-2">
            {allCards.map((c) => (
              <div
                key={c.id}
                className="card cursor-pointer"
                onClick={() => setExpandedCard((id) => (id === c.id ? null : c.id))}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{c.front}</div>
                    <div className="text-xs text-atlas-muted">
                      {courseName(c.course_id) ?? "General"}
                      {c.source_label ? ` · ${c.source_label}` : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {c.next_review_at && new Date(c.next_review_at) <= new Date() && (
                      <Badge tone="warn">due</Badge>
                    )}
                    <button
                      className="text-xs text-atlas-muted hover:text-atlas-bad"
                      onClick={(e) => deleteCard(c.id, e)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                {expandedCard === c.id && (
                  <div className="text-sm text-atlas-muted mt-2 pt-2 border-t border-atlas-border">{c.back}</div>
                )}
              </div>
            ))}
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
  // A class split into linked semester rows (see lib/courseGroups) is still
  // one real class -- without grouping, picking "Class" here showed it
  // twice (e.g. "AP Biology" appearing for both S1 and S2 halves). Grouped
  // down to one option per real class, using whichever half is currently
  // active as the actual id sent to generation/scoping calls.
  const [courses, setCourses] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    apiGet("/courses").then((c) => {
      const active = (c ?? []).filter((x: any) => x.is_active !== false);
      const groups = groupCourses(active);
      setCourses(groups.map((g) => ({ id: currentMember(g).id, name: g.primary.name })));
    });
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
