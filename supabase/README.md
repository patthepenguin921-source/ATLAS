# Atlas · Database

Atlas's **permanent factual memory** lives in Supabase Postgres. Semantic
memory (embeddings) lives in the same database via `pgvector`, so a single
query can join *facts* and *meaning*.

> **Design note — pgvector instead of a separate Qdrant service.** The vision
> names Qdrant for embeddings. We use `pgvector` inside Supabase Postgres
> instead, because it keeps Phase 1 to a single managed datastore (simpler to
> run, backup, and secure) while giving identical cosine-similarity search.
> The embedding access is isolated behind `app/embeddings/` + the
> `match_document_chunks` RPC, so swapping in Qdrant later is a localized
> change, not an architectural one.

## Applying the schema

**Option A — one paste (recommended for a hosted project):**
1. Open **Supabase Dashboard → SQL Editor**.
2. Paste the contents of [`schema.sql`](./schema.sql) and click **Run**.

**Option B — migration by migration:** run the files in
[`migrations/`](./migrations) in numeric order. `schema.sql` is just their
concatenation.

**Option C — Supabase CLI / psql:**
```bash
export DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres"
psql "$DATABASE_URL" -f supabase/schema.sql
```

Everything is idempotent (`create ... if not exists`, guarded enum creation,
`drop policy if exists` before create), so re-running is safe.

## What gets created

| Area | Tables |
|------|--------|
| Identity | `profiles` |
| Structure | `terms`, `teachers`, `courses` |
| Work | `assignments`, `grades`, `calendar_events`, `study_sessions`, `announcements`, `reminders` |
| Documents | `documents`, `document_chunks` (pgvector) |
| Knowledge graph | `concepts`, `concept_edges`, `assignment_concepts`, `document_concepts` |
| Student model | `student_knowledge`, `mistakes` |
| Planning | `daily_plans`, `weekly_reviews`, `progress_metrics` |
| Integrations | `integrations` (Phase 2 scaffold) |
| Agents | `conversations`, `messages` |

Plus RPC functions: `match_document_chunks`, `match_concepts`,
`percentage_to_gpa`, `recompute_course_grade`, `predicted_gpa`.

## Security model

- **Row Level Security** on every table — a user can only read/write rows
  where `user_id = auth.uid()` (`profiles` keyed by `id`).
- The **browser** uses the anon/publishable key and is fully constrained by RLS.
- The **backend** uses the service-role key (bypasses RLS) and always scopes
  queries to the authenticated user it resolved from the JWT.
- Storage bucket `atlas-documents` is **private**; objects are namespaced by
  `‹userId›/…` and policies restrict access to the owning user.

## Embedding dimension

Vector columns are `vector(1024)` (voyage-3 / local fallback). If you switch to
an embedding model with a different dimension, update the `vector(1024)`
columns in `0003_*.sql` and `0006_*.sql`, then re-embed.
