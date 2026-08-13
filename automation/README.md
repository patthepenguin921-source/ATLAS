# Atlas · Automation

The automation layer keeps Atlas continuously updated without manual work.

## PowerSchool & Schoology auto-sync (built in, no extra setup)

If Atlas is deployed on Vercel (`vercel.json` at the repo root already
declares this), both PowerSchool and Schoology sync themselves automatically
twice a day — no n8n, no separate service to run:

1. In the Vercel project settings, set an environment variable
   `CRON_SECRET` to a random string (16+ chars). Vercel automatically sends
   it as `Authorization: Bearer <value>` on every Cron Job request.
2. Set the **same** value as `ATLAS_CRON_SECRET` on the backend service's
   environment variables. The endpoint is disabled until this is set.
3. Deploy. `vercel.json`'s `crons` entries hit
   `GET /api/backend/api/v1/integrations/cron/schoology/sync` and
   `GET /api/backend/api/v1/integrations/cron/powerschool/sync` at 11:00 and
   20:00 UTC — 7am/4pm US Eastern while daylight time is in effect (roughly
   mid-March to early November). Vercel Cron schedules are UTC-only (no IANA
   timezone support), so across the DST boundary this drifts to 6am/3pm
   Eastern until the entries are next adjusted by an hour — still the same
   twice-daily cadence, just shifted. Each sync runs for **every** user who
   has that provider connected and enabled, not just one student. The same
   `crons` block also hits `GET .../agents/cron/daily-plan` (10:00 UTC),
   `GET .../agents/cron/weekly-review` (Sundays 22:00 UTC), and
   `GET .../knowledge/cron/refresh-retention` (07:00 UTC) — see
   `app.services.scheduled_intelligence`'s module docstring for why these
   needed the same "run for every user" treatment the sync endpoints already
   had, instead of only ever firing through the n8n blueprints below. If
   exact Eastern-time firing across DST matters for any of these, use the
   Cloud Run + Cloud Scheduler path below instead — Cloud Scheduler supports
   real `America/New_York` scheduling.
4. Check `GET /api/v1/integrations` (or the Integrations page) for
   `last_synced_at` / `last_error` to confirm it's running, for both
   providers.

Hitting either endpoint manually (e.g. from another scheduler) works the
same way — `curl -X POST .../integrations/cron/schoology/sync -H "X-Cron-Secret: <value>"`
or `curl -X POST .../integrations/cron/powerschool/sync -H "X-Cron-Secret: <value>"`.

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
   This creates nine jobs: the twice-daily Schoology sync and the
   twice-daily PowerSchool sync (each 7am/4pm America/New_York, real IANA
   timezone — no UTC math needed), a daily storage-cleanup sweep (9am)
   that finalizes document deletions — deleting a document in the app
   removes it immediately, but its R2 file itself is only queued for
   removal and stays recoverable for 24h (see `app.services.storage_cleanup`);
   this job is what actually clears it out once that window passes — a
   15-minute document-processing safety-net sweep (see below); and the
   daily plan (6am), weekly review (Sundays 6pm), and retention-decay
   (3am) sweeps, each running for every user the same way the sync jobs
   do. All nine call their endpoint with an `X-Cron-Secret` header — the
   same endpoints accept either that header or Vercel's Bearer-token form,
   so no code changes are needed either way.
3. Once Cloud Run is live, the `crons` block in `vercel.json` becomes dead
   weight (nothing left on Vercel for it to call) — fine to leave or remove.
4. Already ran this script before the storage-cleanup job existed? Re-run
   it (or just create that one job by hand) — it's additive, existing jobs
   are untouched.
5. **Also check the Cloud Run service's own request timeout** (`gcloud run
   services describe <name> --format='value(spec.template.spec.timeoutSeconds)'`,
   or set it with `gcloud run services update <name> --timeout=1800`) — it
   defaults to 300s if never set, and that caps how long a sync request can
   run regardless of anything Cloud Scheduler is configured to wait for.
   The Schoology/PowerSchool sync jobs this script creates now set
   `--attempt-deadline=1800s` (Cloud Scheduler's own default, 180s, was
   already shorter than the backend's own sync budget — `SYNC_TIMEOUT_SECONDS`
   in `app.integrations`, 270s — so a real sync could look like it "failed"
   to the scheduler well before the backend was actually done or had given
   up). Raising the scheduler's deadline alone doesn't help if the Cloud Run
   service itself still kills the request at 300s — `run_sync_for_all` now
   holds every connected user in one sweep to a single shared
   `SYNC_TIMEOUT_SECONDS` budget (not a fresh one per user — with N
   connected users that would add up to N x 270s well past 300s regardless
   of the service timeout), so one sweep should comfortably fit under the
   default 300s even unraised; raising it is still worth doing for headroom
   as the connected-user count grows. Any user not reached before the
   shared budget runs out is simply left alone that sweep (never claimed,
   so never stuck on "running") and picked up by the next scheduled fire —
   each user's own turn is itself chunked and resumable rather than losing
   all progress on a timeout (see `SchoologyProvider.sync`'s docstring).

## Document processing (triggered on upload, not cron-primary anymore)

Uploading a document only stores the file and creates a row (shown as
"processing…" in the UI) — indexing (chunk/embed) and AI enrichment happen
in a separate call to `POST /api/v1/documents/{id}/process`, which the
frontend fires itself immediately after upload/bulk-upload/Drive-import
finishes (fire-and-forget, so the upload response itself stays fast). The
documents page also has an "Index now" button that calls the same endpoint
for anything still `processing…` or `failed` — useful if the post-upload
call never landed (tab closed, network drop) or a prior attempt errored
(the button clears the error and retries; the cron below deliberately does
not, so a stuck document doesn't retry-loop unattended).

`GET/POST /api/v1/documents/cron/process-pending` still exists as a
low-frequency **safety net** behind both of those, not the primary path
anymore: Cloud Scheduler every 15 minutes via `cloud-scheduler-setup.sh`
above, or Vercel Cron once a day per `vercel.json` (Vercel's Hobby plan
rejects any cron schedule that fires more than once a day — `*/5 * * * *`
gets the deploy itself rejected outright, not just throttled).

Neither the on-upload call nor the "Index now" button nor the cron is a
FastAPI `BackgroundTask` kicked off inline with the upload request itself —
that seems like the obvious way to "finish the rest after responding," but
in production on Cloud Run it silently never finished: Cloud Run only
allocates CPU to a container while it's actively serving a request, so a
background task still running after the response has been sent can get
frozen mid-task with no guarantee it's ever scheduled again — the document
just sits at `ingested: false` forever with no error, because the code that
would set one never got to run. Vercel's serverless runtime carries the
same risk. Every one of these three paths instead makes its own real,
separate request to a real endpoint, which always gets genuine CPU for the
call — same reasoning as the sync and storage-cleanup crons.

If uploads seem permanently stuck on "processing" with the "Index now"
button also failing, first suspect the backend itself is unreachable or
erroring (check `GET /api/v1/integrations`-style reachability, or the
button's error response) before assuming the ingestion code regressed.

## n8n blueprints (optional -- Vercel Cron / Cloud Scheduler now cover this natively)

The daily plan, weekly review, and retention refresh used to have no native
scheduler entry at all -- these were the *only* way any of the three ever
ran automatically. That's no longer true: `vercel.json`'s `crons` block and
`cloud-scheduler-setup.sh` both now hit `agents/cron/daily-plan`,
`agents/cron/weekly-review`, and `knowledge/cron/refresh-retention` directly
(see the sections above), each running for every user the same way the
PowerSchool/Schoology sync jobs already did. These importable
[n8n](https://n8n.io) blueprints still work and remain here as an
alternative for a deployment using neither scheduler (or for anyone who
prefers managing schedules through n8n) -- just don't enable both a native
cron entry and the matching n8n workflow for the same job, or it'll run
twice. (`lms-sync.workflow.json` is kept for the same reason, covering a
setup deployed on neither Vercel nor Cloud Run.)

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
| `lms-sync.workflow.json` | 07:00 & 16:00 (America/New_York) | `POST /api/v1/integrations/schoology/sync` and `POST /api/v1/integrations/powerschool/sync` → morning & afternoon PowerSchool + Schoology pull |

Each is a minimal Schedule Trigger → HTTP Request. Extend them to fan out over
multiple students, post results to Slack/email, or chain steps (e.g. sync →
re-plan).
