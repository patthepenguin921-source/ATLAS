# Atlas — Architecture

Atlas is built as a **collection of specialized systems** that share one memory,
rather than a single large model. This document explains how the pieces fit.

```
        ┌──────────────────────────────────────────────────────────┐
        │                       FRONTEND (Next.js)                  │
        │  Dashboard · Courses · Assignments · Documents · Search   │
        │  Knowledge · Analytics · Agent chat                       │
        └───────────────┬──────────────────────────────────────────┘
                        │  HTTPS (Supabase JWT bearer)
        ┌───────────────▼──────────────────────────────────────────┐
        │                 BACKEND · FastAPI (intelligence)          │
        │                                                           │
        │  Routers ──► Services ──► Memory retrieval ──► Claude      │
        │     │            │              │                         │
        │     │            │              └── grounds every prompt  │
        │     │            ├── ingestion (extract·chunk·embed)      │
        │     │            ├── knowledge_model (SM-2 · forgetting)  │
        │     │            └── analytics (GPA · trends · risk)      │
        │     │                                                     │
        │  Agents: Planner · Tutor · Analyst · Archivist · Coach    │
        │  Integrations: Schoology · PowerSchool · Blackboard       │
        └───────┬───────────────────────────────┬──────────────────┘
                │ service-role (bypasses RLS)    │ Anthropic API
        ┌───────▼───────────────┐        ┌───────▼───────┐
        │ Supabase Postgres      │        │    Claude      │
        │  · structured facts    │        │ (reasoning)    │
        │  · pgvector embeddings │        └────────────────┘
        │  · RLS per user        │
        └───────┬────────────────┘        Embeddings: Voyage / OpenAI / local
                │
        ┌───────▼────────────────┐
        │ Cloudflare R2           │
        │  · original files       │
        │  · S3-compatible API    │
        └──────────────────────────┘
```

## Layers

### 1. Memory layer (Supabase Postgres + Cloudflare R2)
The **permanent factual memory**. Every structured entity — courses, teachers,
assignments, grades, quizzes, calendar, study sessions, learning objectives,
progress metrics, daily plans, weekly summaries — is a table, owned per-user and
protected by Row Level Security.

Semantic memory lives in the **same database** via `pgvector`
(`document_chunks.embedding`, `concepts.embedding`). Keeping vectors next to
facts means one query can retrieve "the passages about photosynthesis *and* the
assignments linked to it." The vision named Qdrant; we chose pgvector to keep
Phase 1 to a single datastore, isolated behind `app/embeddings/` + the
`match_document_chunks` RPC so a later swap is localized.

Original files are stored in Cloudflare R2 (S3-compatible object storage —
`app/core/r2_client.py`) and linked from `documents` via `storage_path`. R2's
free tier is 10x Supabase Storage's and has no egress fees; deleting a
`documents` row also removes the underlying R2 object.

### 2. Intelligence layer (Claude)
Claude is the **reasoning engine, not the memory**. The flow for any grounded
request:

1. `services/memory.build_context()` retrieves the relevant slice of the
   student's state — courses, upcoming/overdue work, recent grades, concepts due
   for review, unresolved mistakes, and semantically-matched document passages.
2. `services/memory.render_context()` renders it into a compact system prompt.
3. Claude reasons over that grounded context and returns an answer or structured
   JSON (plans, quizzes, analyses).

This guarantees answers reflect the student's actual history, not just the chat.

### 3. Agent system
Five specialists, one shared memory (`agents/base.Agent`):

| Agent | Responsibility | Key output |
|-------|----------------|-----------|
| **Planner** | Daily schedule, prioritization, anti-procrastination | `daily_plans` row (time-blocked) |
| **Tutor** | Explanations, quizzes, active recall, spaced repetition | explanations, quiz JSON |
| **Analyst** | Trends, retention, risk, prediction | analytics + narrative |
| **Archivist** | Organize files, extract metadata, build knowledge graph | doc summary/keywords + concept links |
| **Coach** | Accountability, weekly reviews, strategy | `weekly_reviews` row |

### 4. Automation layer (n8n)
Scheduled workflows call the backend to generate the morning plan, produce the
weekend review, refresh retention estimates, and (Phase 2) sync LMS data. See
[`automation/`](../automation).

## The student knowledge model
`student_knowledge` holds per-concept `confidence`, `mastery`, `retention`, and
an SM-2 spaced-repetition schedule. Retention decays on a forgetting curve
(`R = e^(−t/S)`), so Atlas can predict *when* a concept will be forgotten and
surface it for review beforehand. Grades feed the model automatically
(`knowledge_model.observe_grade`).

## Security model
- **RLS everywhere** — a user can only touch rows where `user_id = auth.uid()`.
- **Browser** uses the anon key → fully constrained by RLS; it never talks to
  storage directly.
- **Backend** uses the service-role key (bypasses RLS) and always scopes queries
  to the user id it resolved from the verified JWT. The same user-id scoping
  (not RLS) is what protects R2 objects, since the backend is the only caller.
- **Storage** bucket is private; objects namespaced by `‹userId›/…`, accessed
  via short-lived signed URLs.
- **Secrets** live only in environment variables, never in the repo.

## Path to AWS
Interfaces are thin on purpose: Storage already moved off Supabase to
Cloudflare R2, which speaks the S3 API directly (`app/core/r2_client.py`), so a
later hop to AWS S3 itself is a config change, not a rewrite. Auth (Supabase
Auth → Cognito) and vectors (pgvector → managed vector DB) remain similarly
swappable without touching business logic.
