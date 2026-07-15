# Pipeline Smoke Test — PLY-279

Completed: 2026-07-15

## Services Tested

| Service | Port | Endpoint | Status |
|---------|------|----------|--------|
| Router | 8670 | /triage | 200 OK — triage decision posted as comment |
| Planner | 8663 | /plan | 200 OK — plan comment posted, state → Planned |
| Executor | 8664 | /execute | Running |

## Router Smoke Test

```bash
curl -s -X POST http://127.0.0.1:8670/triage \
  -H "Content-Type: application/json" \
  -d '{"issue_id": "ISSUE_UUID"}' \
  --max-time 120
```

Expected: `{"status":"blocked","comment":"Blocked: ...","labels":["blocked"]}`
Result: ✅ Comment posted to PLY-279, issue state unchanged (Blocked)

## Planner Smoke Test

```bash
curl -s -X POST http://127.0.0.1:8663/plan \
  -H "Content-Type: application/json" \
  -d '{"issue_id": "ISSUE_UUID"}' \
  --max-time 120
```

Expected: `{"summary":"**Planner complete for PLY-279.**\\n\\nPlan posted as comment..."}`
Result: ✅ Plan comment posted, issue moved to Planned state

## Verification

Both services:
- `GET /health` returns `{"status":"ok"}`
- LLM backend (Hermes API on :8642) reachable
- Linear GraphQL mutations succeed (comments + state transitions)

## Issue

PLY-279 has been moved to **Done** (completed). Closing comment posted.
