# Atlas · Automation

The automation layer keeps Atlas continuously updated without manual work.

## Schoology auto-sync (built in, no extra setup)

If Atlas is deployed on Vercel (`vercel.json` at the repo root already
declares this), Schoology syncs itself automatically twice a day — no n8n,
no separate service to run:

1. In the Vercel project settings, set an environment variable
   `CRON_SECRET` to a random string (16+ chars). Vercel automatically sends
   it as `Authorization: Bearer <value>` on every Cron Job request.
2. Set the **same** value as `ATLAS_CRON_SECRET` on the backend service's
   environment variables. The endpoint is disabled until this is set.
3. Deploy. `vercel.json`'s `crons` entries hit
   `GET /api/backend/api/v1/integrations/cron/schoology/sync` at 10:00 and
   20:00 UTC — 6am/4pm US Eastern while daylight time is in effect (roughly
   mid-March to early November). Vercel Cron schedules are UTC-only (no IANA
   timezone support), so across the DST boundary this drifts to 5am/3pm
   Eastern until the entries are next adjusted by an hour — still the same
   twice-daily cadence, just shifted. Syncs **every** user who has Schoology
   connected and enabled, not just one student. If exact Eastern-time firing
   across DST matters, use the Cloud Run + Cloud Scheduler path below
   instead — Cloud Scheduler supports real `America/New_York` scheduling.
4. Check `GET /api/v1/integrations` (or the Integrations page) for
   `last_synced_at` / `last_error` to confirm it's running.

Hitting the endpoint manually (e.g. from another scheduler) works the same
way — `curl -X POST .../integrations/cron/schoology/sync -H "X-Cron-Secret: <value>"`.

## Cloud Run + Cloud Scheduler (if you're moving the backend off Vercel)

Vercel's "auto-inject the secret as a Bearer header" trick is Vercel-specific
and only fires from Vercel's own Cron Jobs — it does nothing on Cloud Run.
If the backend runs on Cloud Run instead:

1. Set `ATLAS_CRON_SECRET` as an env var on the Cloud Run service — ideally
   via Secret Manager (`gcloud run services update <name>
   --update-secrets=ATLAS_CRON_SECRET=your-secret:latest`) rather than
   plaintext.
2. Cloud Run has no cron of its own, so **Cloud Scheduler** is what actually
   calls the endpoint. Run `automation/cloud-scheduler-setup.sh` (needs the
   `gcloud` CLI, authenticated and pointed at your project):
   ```bash
   PROJECT_ID=my-gcp-project \
   CLOUD_RUN_URL=https://atlas-backend-xyz.a.run.app \
   CRON_SECRET=the-same-value-as-ATLAS_CRON_SECRET \
   ./automation/cloud-scheduler-setup.sh
   ```
   This creates three jobs: the twice-daily Schoology sync (7am/4pm
   America/New_York, real IANA timezone — no UTC math needed), and a daily
   storage-cleanup sweep (9am) that finalizes document deletions — deleting
   a document in the app removes it immediately, but its R2 file itself is
   only queued for removal and stays recoverable for 24h (see
   `app.services.storage_cleanup`); this job is what actually clears it out
   once that window passes. All three call their endpoint with an
   `X-Cron-Secret` header — the same endpoints accept either that header or
   Vercel's Bearer-token form, so no code changes are needed either way.
3. Once Cloud Run is live, the `crons` block in `vercel.json` becomes dead
   weight (nothing left on Vercel for it to call) — fine to leave or remove.
4. Already ran this script before the storage-cleanup job existed? Re-run
   it (or just create that one job by hand) — it's additive, existing jobs
   are untouched.

## n8n blueprints (for jobs with no native scheduler yet)

The daily plan, weekly review, and retention refresh don't have a Vercel Cron
entry yet, so these importable [n8n](https://n8n.io) workflow blueprints
still cover them (an `lms-sync.workflow.json` blueprint is kept here too, for
setups not deployed on Vercel).

## Setup

1. Run n8n (`docker run -it --rm -p 5678:5678 n8nio/n8n`).
2. **Import** each JSON in this folder (Workflows → Import from File).
3. Create an **HTTP Header Auth** credential named `Atlas Backend`:
   - Header: `Authorization`
   - Value: `Bearer <a long-lived Supabase access token for the student>`
   > Tip: for headless automation, mint a service token per user or run these
   > flows server-side where you can attach the user's session. For local dev
   > with no JWT secret set, use header `X-Atlas-Dev-User: <uuid>` instead.
4. Set the `ATLAS_API` environment variable in n8n to your backend base URL
   (e.g. `http://host.docker.internal:8000`).

## Blueprints

| File | Schedule | What it does |
|------|----------|--------------|
| `daily-plan.workflow.json` | every day 06:00 | `POST /api/v1/agents/planner/daily-plan` → generates the day's plan |
| `weekly-review.workflow.json` | Sundays 18:00 | `POST /api/v1/agents/coach/weekly-review` → weekend review |
| `refresh-retention.workflow.json` | every day 03:00 | `POST /api/v1/knowledge/refresh-retention` → decays retention estimates |
| `lms-sync.workflow.json` | 06:00 & 16:00 (America/New_York) | `POST /api/v1/integrations/schoology/sync` → morning & afternoon Schoology pull |

Each is a minimal Schedule Trigger → HTTP Request. Extend them to fan out over
multiple students, post results to Slack/email, or chain steps (e.g. sync →
re-plan).
