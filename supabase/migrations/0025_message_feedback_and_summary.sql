-- =====================================================================
-- ATLAS — 0025 · Message feedback + rolling conversation summaries
--
-- Two independent, additive changes:
--
-- 1. `messages.feedback` / `feedback_note` -- a thumbs up/down (+ optional
--    reason) a student can leave on any assistant reply. This is Atlas's
--    human-feedback signal: it doesn't retrain anything itself (Atlas runs
--    on hosted Groq/Gemini/Claude, there's no model to fine-tune here), but
--    it's the concrete substrate for "training" Atlas to be better --
--    surfacing which replies actually land lets prompts/personas/tools be
--    iterated on with real signal instead of guesswork, and a `feedback =
--    'down'` reply is exactly the set worth reading first.
--
-- 2. `conversations.summary` -- a rolling, best-effort summary of the
--    *older* turns in a long conversation (see MemoryKeeper's sibling,
--    ConversationSummarizer, in app.agents). `app.routers.agents.chat` only
--    ever sends an agent the most recent N raw messages as history (context-
--    window + cost reasons) -- past that window a long-running conversation
--    used to just silently lose everything earlier. The summary is what
--    stands in for the dropped turns, refreshed every so often as the
--    conversation grows instead of on every single turn.
-- =====================================================================

alter table public.messages
  add column if not exists feedback text
    check (feedback is null or feedback in ('up', 'down')),
  add column if not exists feedback_note text;

alter table public.conversations
  add column if not exists summary text,
  -- how many messages were already folded into `summary` -- lets the
  -- summarizer know whether enough *new* turns have accumulated since the
  -- last pass to be worth another model call, without re-counting messages
  -- every turn.
  add column if not exists summary_through_count integer not null default 0;
