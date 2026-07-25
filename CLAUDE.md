# Atlas — project notes for Claude

## Stack

- **Backend**: FastAPI (`backend/`), Supabase Postgres for data + auth (JWTs),
  Cloudflare R2 for file storage (moved off Supabase Storage — see
  `docs/ARCHITECTURE.md`).
- **Frontend**: Next.js (`frontend/`).
- **Auth/DB is Supabase, not Firebase/Firestore** — no Firestore, no
  Firebase Auth anywhere. That said, **`frontend/apphosting.yaml` is a real
  Firebase App Hosting config** (correcting an earlier wrong claim in this
  file that "no Firebase exists" — it does, just for frontend hosting, not
  data/auth). It deploys the Next.js frontend only and its
  `NEXT_PUBLIC_API_BASE_URL` already points at a live Cloud Run backend URL
  (`atlas-backend-*.us-east4.run.app`), meaning the backend in that
  configuration is Cloud Run, not Vercel — check which `NEXT_PUBLIC_API_BASE_URL`
  is actually live before assuming either hosting path.

## Deployment

Three hosting pieces can combine — figure out which are actually live
before assuming Cron/scheduling behavior, since it changes which scheduler
fires the sync jobs:

- **Frontend**: either Vercel (`vercel.json`'s `services.frontend`) or
  **Firebase App Hosting** (`frontend/apphosting.yaml`). Both just serve the
  Next.js app and point it at whatever `NEXT_PUBLIC_API_BASE_URL` is set to.
- **Backend**: either a Vercel service (`vercel.json`'s `services.backend`,
  FastAPI proxied under `/api/backend/*`) or **Google Cloud Run**
  (`atlas-backend-*.us-east4.run.app` per `apphosting.yaml` — this is the
  URL currently wired up as of this writing).
- **Scheduler** (whichever calls the cron endpoints depends on where the
  backend lives, not the frontend):
  - Backend on Vercel → Vercel Cron entries in `vercel.json`'s `crons`
    array (UTC only, no IANA timezone support, drifts an hour across DST).
  - Backend on Cloud Run → Cloud Run has no cron of its own, so **Google
    Cloud Scheduler** calls the endpoints instead, via
    `automation/cloud-scheduler-setup.sh` (real `America/New_York`
    scheduling, no DST drift).

All paths call the same backend endpoints, secured by `ATLAS_CRON_SECRET`
(see `app.core.security.check_cron_secret`) — no backend code differs
between them, only which scheduler triggers the HTTP call.

## Automated PowerSchool + Schoology sync

`GET/POST /api/v1/integrations/cron/{provider}/sync` runs a given
provider's sync for every user who has it connected & enabled. Both
`powerschool` and `schoology` are wired up on both hosting paths, firing
twice daily at ~7:00 AM and ~4:00 PM America/New_York:

- Vercel: `vercel.json` → `crons` (11:00 & 20:00 UTC — drifts an hour across
  DST since Vercel Cron is UTC-only).
- Cloud Run: `automation/cloud-scheduler-setup.sh` → creates
  `atlas-{powerschool,schoology}-sync-{morning,afternoon}` jobs plus
  `atlas-storage-cleanup` and `atlas-document-processing`.
- n8n (fallback, works regardless of host): `automation/lms-sync.workflow.json`.

Full details: `automation/README.md`.

## Document upload/indexing is cron-driven, not inline — and NOT a background task

`POST /documents/upload` (and bulk-upload/Drive-import) only stores the
file + creates a row (`ingested: false`, shown as "processing…") — it does
**not** chunk/embed or AI-enrich inline, and does **not** hand that off to a
FastAPI `BackgroundTask` either, even though that looks like the obvious fix
for "don't block the response." A `GET/POST
/documents/cron/process-pending` cron (same `ATLAS_CRON_SECRET` pattern,
scheduled every 2–5 min) claims pending documents and does the actual
chunk/embed/enrich work.

**Why not just a `BackgroundTask`**: tried that first — it made the upload
response fast, but on Cloud Run the document would sit at `ingested: false`
forever with no error. Cloud Run only allocates CPU to a container while
it's actively serving a request; a background task still running after the
response is sent can get frozen mid-task with no guarantee it's ever
scheduled again, so the code path that would eventually set
`ingest_error` never got to run. Vercel's serverless runtime has the same
risk. A scheduler hitting a real endpoint on an interval always gets
genuine CPU for that call — same reasoning as every other cron in this
app — so don't reach for `BackgroundTasks` here again without solving that
problem first (e.g. confirming "CPU always allocated" is on for the Cloud
Run service).

If someone reports an upload "stuck processing": first check whether the
`process-pending` scheduler is actually configured/deployed (the most
common cause) before suspecting the ingestion code itself — see
`automation/README.md`'s "Document processing" section.
