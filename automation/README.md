# Atlas · Automation (n8n)

The automation layer keeps Atlas continuously updated without manual work.
These are importable [n8n](https://n8n.io) workflow blueprints that call the
Atlas backend on a schedule.

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
| `lms-sync.workflow.json` | every 4 hours | `POST /api/v1/integrations/schoology/sync` (Phase 2) |

Each is a minimal Schedule Trigger → HTTP Request. Extend them to fan out over
multiple students, post results to Slack/email, or chain steps (e.g. sync →
re-plan).
