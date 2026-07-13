# Atlas · Frontend

Next.js 14 (App Router) + React + Tailwind. Supabase Auth in the browser; all
data flows through the Atlas backend.

## Run locally

```bash
cd frontend
npm install
# set env (see root .env.example → NEXT_PUBLIC_* vars)
cp .env.local.example .env.local   # then fill in values
npm run dev                        # http://localhost:3000
```

Required env (`.env.local`):

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## Pages

| Route | Purpose |
|-------|---------|
| `/login` | Supabase email/password auth |
| `/` | Daily dashboard — briefing, priorities, at-risk, plan |
| `/courses` | Course list + grades |
| `/assignments` | Track & update assignment status |
| `/documents` | Upload → ingest → semantic memory |
| `/search` | Natural-language grounded search ("ask Atlas") |
| `/knowledge` | Student knowledge model + spaced-repetition review |
| `/analytics` | GPA, trends, at-risk, Analyst report |
| `/chat` | Five agents (Planner/Tutor/Analyst/Coach/general) |

## Deploy (Vercel)

Import the repo, set root directory to `frontend`, add the `NEXT_PUBLIC_*`
environment variables, and deploy. Point `NEXT_PUBLIC_API_BASE_URL` at your
deployed backend.
