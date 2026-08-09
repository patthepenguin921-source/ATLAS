# Atlas · Backend (Intelligence Layer)

FastAPI service that grounds the reasoning engine in the student's real
academic memory and exposes the multi-agent system.

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env         # then fill in real values
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

- Works **keyless** for development: without Supabase/LLM keys the app still
  boots (`/health` reports what's configured). Embeddings fall back to a local
  deterministic encoder. Add keys to unlock persistence + reasoning.
- Reasoning defaults to **Groq's free tier** (`ATLAS_LLM_PROVIDER=groq` +
  `GROQ_API_KEY`, model IDs pinned in `ATLAS_GROQ_MODEL`/`ATLAS_GROQ_FAST_MODEL`
  — Groq has no floating "-latest" alias and does retire model IDs on notice,
  see `app.config.Settings.atlas_groq_model`'s comment and
  https://console.groq.com/docs/deprecations if chat stops working).
  **Google Gemini** is also free — get a key at
  https://aistudio.google.com/apikey and set `GEMINI_API_KEY` to enable it as
  the automatic fallback (default `ATLAS_LLM_FALLBACK_PROVIDER=gemini`): a
  429 (or Groq's configured model having been retired server-side) from the
  primary provider retries once against Gemini instead of failing the chat
  turn, so it only fails if both free tiers are exhausted (or broken) at
  once. Set `ATLAS_LLM_PROVIDER=gemini` to make it the primary provider
  instead of the fallback — generally stronger reasoning than Groq's default
  model, with materially higher free-tier rate limits than Groq's, and no
  pinned-model-ID risk (see `atlas_gemini_model`'s own comment). Set
  `ATLAS_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` to use Claude instead
  (paid, highest quality).
- Dev auth shortcut: when `ATLAS_ENV=development` and Supabase isn't
  configured, pass `X-Atlas-Dev-User: <uuid>` to act as a user without a real
  token.
- Auth tokens are verified by calling Supabase's own `/auth/v1/user` endpoint
  rather than decoding the JWT locally — this works regardless of whether the
  project uses a legacy JWT secret or the newer asymmetric signing keys, so
  there's nothing extra to configure.

## Tests

```bash
pytest -q            # runs without any external services
```

## Layout

```
app/
  main.py            FastAPI app + router mounting
  config.py          env-driven settings
  core/              supabase client, R2 storage client, JWT auth, generic CRUD factory
  llm/claude.py      grounded reasoning wrapper (pluggable: groq | gemini | anthropic, with fallback)
  embeddings/        pluggable embeddings (voyage | openai | local)
  services/          memory retrieval, ingestion, knowledge model, analytics
  agents/            Planner, Tutor, Analyst, Archivist, Coach (+ base/registry)
  routers/           REST endpoints (CRUD + intelligence)
  integrations/      PowerSchool (live) + Schoology / Blackboard scaffolds (Phase 2)
```

## API surface (under `/api/v1`)

| Area | Endpoints |
|------|-----------|
| Profile | `GET/PATCH /profile` |
| Structure | `/terms` `/teachers` `/courses` (CRUD) |
| Work | `/assignments` `/grades` `/calendar` `/study-sessions` `/announcements` `/mistakes` `/reminders` (CRUD) |
| Documents | `POST /documents/upload`, `POST /documents/ingest-text`, list/get/delete |
| Search | `POST /search/semantic`, `POST /search/ask`, `GET /search/text` |
| Dashboard | `GET /dashboard` |
| Knowledge | `/knowledge/concepts` `/knowledge/graph` `/knowledge/model` `/knowledge/review-queue` `POST /knowledge/review` |
| Analytics | `/analytics/snapshot` `/gpa` `/trends` `/at-risk` `/study-efficiency` |
| Agents | `POST /agents/chat`, `/agents/planner/daily-plan`, `/agents/tutor/explain`, `/agents/tutor/quiz`, `/agents/analyst/analyze`, `/agents/coach/weekly-review` |
| Reviews | `GET /reviews/weekly` `GET /reviews/plans` |
| Integrations | `GET /integrations/providers`, `POST /integrations/{provider}/sync` |
```
