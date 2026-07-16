# Atlas — Academic Operating System

> A persistent academic intelligence — a *second brain* for school. Atlas
> observes, remembers, analyzes, predicts, teaches, and improves every part of
> your learning. Not a chatbot. Not a note app. An operating system for your
> academic life.

Atlas turns every assignment into data, every quiz into feedback, every mistake
into a learning opportunity, and every document into searchable knowledge — so
you can spend your mental energy actually learning instead of staying organized.

---

## What's in this repo

```
ATLAS/
├── supabase/        Postgres schema — Atlas's permanent factual + semantic memory
│   ├── schema.sql       one-paste full schema
│   └── migrations/      numbered, idempotent migrations
├── backend/         FastAPI intelligence layer (Claude reasoning + agents)
│   └── app/
│       ├── agents/      Planner · Tutor · Analyst · Archivist · Coach
│       ├── services/    memory retrieval · ingestion · knowledge model · analytics
│       ├── llm/         grounded Claude wrapper
│       ├── embeddings/  pluggable (voyage | openai | local)
│       └── integrations/Schoology · PowerSchool · Blackboard (Phase 2 scaffolds)
├── frontend/        Next.js + React + Tailwind app
├── automation/      n8n workflow blueprints (daily plan, weekly review, sync)
├── docs/            architecture & roadmap
└── docker-compose.yml
```

## Architecture at a glance

Atlas is a **collection of specialized systems**, not one monolithic AI:

- **Memory layer** — Supabase Postgres stores structured facts (courses,
  assignments, grades, calendar, study sessions, the knowledge graph, the
  student knowledge model). `pgvector` stores semantic embeddings in the *same*
  database, so a single query can join facts and meaning. Original files live in
  Cloudflare R2 (S3-compatible object storage — 10 GB free, no egress fees).
- **Intelligence layer** — Claude is the reasoning engine. It is *not* the
  memory: every response is grounded in the student's real academic history,
  retrieved from the databases first.
- **Agent system** — five specialists sharing one memory: **Planner**
  (schedules & priorities), **Tutor** (active recall + spaced repetition),
  **Analyst** (trends, risk, prediction), **Archivist** (organizes files,
  builds the knowledge graph), **Coach** (accountability, weekly reviews).
- **Automation layer** — n8n blueprints keep Atlas updated (daily plans, weekly
  reviews, LMS sync) without manual work.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full picture and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase plan.

## Quick start

1. **Database** — open Supabase → SQL Editor → paste [`supabase/schema.sql`](supabase/schema.sql) → Run.
   (Details + design notes in [`supabase/README.md`](supabase/README.md).)
2. **Backend**
   ```bash
   cd backend && python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp ../.env.example ../.env      # fill in Supabase + R2 + Anthropic keys
   uvicorn app.main:app --reload   # http://localhost:8000/docs
   ```
3. **Frontend**
   ```bash
   cd frontend && npm install
   cp .env.local.example .env.local   # fill in NEXT_PUBLIC_* values
   npm run dev                         # http://localhost:3000
   ```

Or bring up the backend with Docker: `docker compose up backend`.

> **Runs keyless for development.** Without keys the backend still boots and the
> embeddings fall back to a local encoder. Add your Supabase, R2, and Anthropic
> keys to unlock persistence, file storage, and reasoning.

## Configuration

All configuration is via environment variables — see [`.env.example`](.env.example).
Secrets never live in the repo. The backend uses the Supabase **service-role**
key (server-side only) for Postgres/auth, and R2 API credentials (also
server-side only) for document storage; the browser uses the Supabase
**anon/publishable** key for auth and is fully constrained by Row Level
Security — it never talks to storage directly.

## Design decisions worth knowing

- **pgvector instead of a standalone Qdrant service.** Same cosine search, one
  fewer datastore to run in Phase 1; isolated behind `app/embeddings/` + an RPC
  so swapping to Qdrant later is localized.
- **Embeddings are pluggable.** Anthropic doesn't provide embeddings, so Atlas
  supports Voyage (recommended) or OpenAI, with a keyless local fallback for
  dev/CI.
- **Storage on Cloudflare R2, not Supabase Storage.** Same S3-compatible shape
  the architecture was already designed around (`app/core/r2_client.py`), just
  with 10x the free storage and no egress fees. Auth/vectors still sit behind
  thin interfaces so they can move (e.g. to Cognito / a managed vector DB)
  without an architectural rewrite.

## Status

Phase 1 (auth, database, documents + semantic search, assignment/grade
tracking) and much of Phase 3 (daily planning, weekly reviews, knowledge graph,
student knowledge model, adaptive review) are implemented. Phase 2 LMS
integrations are scaffolded with a clear contract; Phase 4 predictive analytics
are partially implemented (predicted GPA, risk scoring, trend detection). See
the roadmap for exactly what's done vs. next.
