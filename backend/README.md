# Atlas · Backend (Intelligence Layer)

FastAPI service that grounds Claude in the student's real academic memory and
exposes the multi-agent system.

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env         # then fill in real values
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

- Works **keyless** for development: without Supabase/Claude keys the app still
  boots (`/health` reports what's configured). Embeddings fall back to a local
  deterministic encoder. Add keys to unlock persistence + reasoning.
- Dev auth shortcut: when `ATLAS_ENV=development` and no `SUPABASE_JWT_SECRET`
  is set, pass `X-Atlas-Dev-User: <uuid>` to act as a user without a real JWT.

## Tests

```bash
pytest -q            # runs without any external services
```

## Layout

```
app/
  main.py            FastAPI app + router mounting
  config.py          env-driven settings
  core/              supabase client, JWT auth, generic CRUD factory
  llm/claude.py      grounded reasoning wrapper (Claude)
  embeddings/        pluggable embeddings (voyage | openai | local)
  services/          memory retrieval, ingestion, knowledge model, analytics
  agents/            Planner, Tutor, Analyst, Archivist, Coach (+ base/registry)
  routers/           REST endpoints (CRUD + intelligence)
  integrations/      Schoology / PowerSchool / Blackboard scaffolds (Phase 2)
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
