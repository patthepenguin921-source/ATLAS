# Atlas — Roadmap & Status

Every phase produces a usable application. Status reflects what's in this repo.

## Phase 1 — Foundation ✅ (implemented)
- [x] Authentication (Supabase Auth; JWT verified server-side)
- [x] Database (full schema, RLS, triggers, RPC functions)
- [x] Document upload → storage → extraction → chunking → embedding
- [x] Assignment tracking (unified categories + status lifecycle)
- [x] Grade tracking (auto percentage, rolling course grade, GPA)
- [x] Semantic search + natural-language grounded "ask Atlas"
- [x] Frontend: dashboard, courses, assignments, documents, search

## Phase 2 — Integrations 🚧 (PowerSchool live, others scaffolded)
- [x] **PowerSchool** — logs into the Guardian/Student portal (unofficial,
      since individual students don't get OAuth district credentials) and
      imports courses, current grades, and per-assignment scores. Portal
      login is stored encrypted (`app/core/crypto.py`); see
      `integrations/powerschool_client.py` for the login handshake and its
      caveats (the assignment-detail scraping is best-effort and may need a
      selector tweak for a given district's PowerSchool version).
      Two auth modes: username/password (districts with PowerSchool's native
      login form) and a pasted session cookie (districts that gate
      PowerSchool behind SSO — Google/Microsoft/Clever — where there's no
      login form to automate at all; the cookie expires and needs periodic
      re-pasting, so this mode is semi- rather than fully automatic).
- [~] Schoology / Blackboard providers — orchestration, normalization, and
      persistence contract defined; concrete API clients are clearly-marked
      stubs (require per-district OAuth/credentials).
- [ ] Calendar synchronization (schema + CRUD ready; provider push/pull pending)
- [x] Automatic document ingestion pipeline (usable now via upload/ingest-text)
- [x] n8n workflow blueprints for scheduled sync

**Next:** wire a scheduled PowerSchool sync (n8n or a cron endpoint) instead
of manual/on-connect only, and implement Schoology (REST + OAuth, most
approachable of the remaining two) through `integrations/base.py`.

## Phase 3 — Adaptive intelligence ✅ (largely implemented)
- [x] Daily planning (Planner agent → `daily_plans`)
- [x] Weekly reviews (Coach agent → `weekly_reviews`)
- [x] Adaptive studying (SM-2 spaced repetition + review queue)
- [x] Weakness detection (retention decay, unresolved mistakes, at-risk scoring)
- [x] Knowledge graph (`concepts` + `concept_edges`, Archivist auto-links)

**Next:** richer graph construction (cross-course edges, prerequisite inference)
and a graph visualization in the UI (API `/knowledge/graph` already returns
nodes+edges).

## Phase 4 — Predictive & multi-agent 🚧 (partial)
- [x] Predicted GPA, risk analysis, grade-trend detection
- [x] Multi-agent system with shared memory (5 agents)
- [ ] Long-term performance forecasting (semester/AP-score models)
- [ ] Agent-to-agent collaboration (Planner consulting Analyst, etc.)
- [ ] Continuous optimization loop (auto-tuning study strategy from outcomes)

**Next:** a forecasting service over `progress_metrics` time-series, and an
orchestrator that lets agents call one another.

---

## How the pieces map to endpoints

| Capability | Endpoint |
|-----------|----------|
| Morning briefing | `GET /dashboard` |
| Generate today's plan | `POST /agents/planner/daily-plan` |
| Explain / quiz | `POST /agents/tutor/explain`, `/quiz` |
| Performance analysis | `POST /agents/analyst/analyze`, `GET /analytics/*` |
| Organize a file | `POST /documents/upload` (Archivist enriches) |
| Weekly review | `POST /agents/coach/weekly-review` |
| Ask anything | `POST /search/ask` |
| Review a concept | `POST /knowledge/review` |
| Connect PowerSchool | `POST /integrations/powerschool/connect` (also runs the first sync) |
| Sync an LMS | `POST /integrations/{provider}/sync` |
| Disconnect an integration | `DELETE /integrations/{provider}` |
