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
   `GET /api/backend/api/v1/integrations/cron/schoology/sync` at 07:00 and
   16:00 UTC (≈ 7am/4pm US Eastern; shifts an hour across the DST boundary —
   still the same twice-daily cadence) and sync **every** user who has
   Schoology connected and enabled, not just one student.
4. Check `GET /api/v1/integrations` (or the Integrations page) for
   `last_synced_at` / `last_error` to confirm it's running.

Hitting the endpoint manually (e.g. from another scheduler) works the same
way — `curl -X POST .../integrations/cron/schoology/sync -H "X-Cron-Secret: <value>"`.

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
| `lms-sync.workflow.json` | 07:00 & 16:00 (America/New_York) | `POST /api/v1/integrations/schoology/sync` → morning & afternoon Schoology pull |

Each is a minimal Schedule Trigger → HTTP Request. Extend them to fan out over
multiple students, post results to Slack/email, or chain steps (e.g. sync →
re-plan).
