# Pipeline Smoke Test — PLY-279

Completed: 2026-07-15

## Services Tested

| Service | Port | Endpoint | Status |
|---------|------|----------|--------|
| Router | 8670 | /triage | 200 OK — triage decision posted as comment |
| Planner | 8663 | /plan | 200 OK — plan comment posted, state changed to Planned |
| Executor | 8664 | /execute | Scaffolded |
| Critic | 8665 | /review | Scaffolded |

## Step 3: Router E2E Smoke Test

POST /triage with PLY-279:
- Response: `{"status":"blocked","comment":"Blocked: No repo URL, no acceptance criteria...","labels":["blocked"]}`
- Result: 200 OK, comment posted to PLY-279
- Services restarted with proper `run.sh` environment loading

## Step 4: Planner E2E Smoke Test

POST /plan with PLY-279:
- Response: `{"summary":"Done. Here's the summary:\n\n**PLY-279 — Smoke test todo**\n\nPlan posted to the issue..."}`
- Result: 200 OK, plan comment posted, issue state transitioned to Planned

## Step 5: Cleanup

- Closing comment posted to PLY-279
- Issue moved to **Done** state
- Synthetic smoke-test artifact closed

## Verification

Both services:
- GET /health returns `{"status":"ok"}`
- LLM backend (Hermes API on :8642) reachable
- Linear GraphQL mutations succeed (comments + state transitions)
- Services properly started via `run.sh` (venv activation, env loading, PYTHONPATH)

## Issue

PLY-279 has been moved to **Done** (completed). Closing comment posted.
