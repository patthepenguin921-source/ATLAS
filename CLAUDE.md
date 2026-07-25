# Atlas — project notes for Claude

## Stack

- **Backend**: FastAPI (`backend/`), Supabase Postgres for data + auth (JWTs),
  Cloudflare R2 for file storage (moved off Supabase Storage — see
  `docs/ARCHITECTURE.md`).
- **Frontend**: Next.js (`frontend/`).
- **No Firebase anywhere in this project.** Auth/DB is Supabase, not
  Firebase/Firestore. If a task mentions Firebase, confirm with the user
  whether they mean Supabase or an actual separate Firebase project before
  assuming one exists.

## Deployment

Two supported hosting paths for the backend — pick based on what's actually
deployed before assuming Cron/scheduling behavior:

1. **Vercel (default)** — `vercel.json` at repo root deploys both
   `frontend` and `backend` as Vercel services, with `backend`'s FastAPI app
   proxied under `/api/backend/*`. Scheduled jobs are Vercel Cron entries in
   `vercel.json`'s `crons` array (UTC only, no IANA timezone support).
2. **Google Cloud Run** — if the backend is moved off Vercel, Cloud Run has
   no cron of its own, so **Google Cloud Scheduler** calls the endpoints
   instead, via `automation/cloud-scheduler-setup.sh` (supports real
   `America/New_York` scheduling, so it doesn't drift across DST the way
   the Vercel UTC crons do).

Both paths call the same backend endpoints, secured by `ATLAS_CRON_SECRET`
(see `app.core.security.check_cron_secret`) — no code differs between the
two, only which scheduler triggers the HTTP call.

## Automated PowerSchool + Schoology sync

`GET/POST /api/v1/integrations/cron/{provider}/sync` runs a given
provider's sync for every user who has it connected & enabled. Both
`powerschool` and `schoology` are wired up on both hosting paths, firing
twice daily at ~7:00 AM and ~4:00 PM America/New_York:

- Vercel: `vercel.json` → `crons` (11:00 & 20:00 UTC — drifts an hour across
  DST since Vercel Cron is UTC-only).
- Cloud Run: `automation/cloud-scheduler-setup.sh` → creates
  `atlas-{powerschool,schoology}-sync-{morning,afternoon}` jobs plus
  `atlas-storage-cleanup`.
- n8n (fallback, works regardless of host): `automation/lms-sync.workflow.json`.

Full details: `automation/README.md`.
