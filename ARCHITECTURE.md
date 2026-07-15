# Linear-Hermes Agent Pipeline — Prototype Architecture

Goal
----
Build a minimal 4-stage agent pipeline for Linear issues:
  Router → Planner → Executor → Critic

Each stage is a standalone FastAPI service with its own systemd unit, port,
prompt, and scoped permissions. Stages communicate through Linear state
changes, not direct RPC.

Current Services
----------------
- `openhands-linear-agent` on 8662 — OpenHands backend, already stable, out of scope for v1 prototype
- `linear-agent` on 8660 — Hermes full-session agent, currently healthy, will be replaced by dispatcher

Prototype Services
------------------
Service          Port  Role
---------------- -----  -----------------------------------------------
dispatcher       8660  Receives Linear webhook, routes by state
router           8661  Verifies repo/AC/scope, emits ready or blocked
planner          8663  Breaks issue into steps, provisions infra, labels planned
executor         8664  Implements plan, runs tests, opens PR, labels in-review
critic           8665  Reviews diff, scans security/deps, enforces merge gate

State Contract
--------------
  needs-triage → dispatcher → router
  blocked      → dispatcher → router
  ready        → dispatcher → router     (skip if already ready? no — verify every time)
  planned      → dispatcher → executor   (planner labels planned)
  in-review    → dispatcher → critic     (executor labels in-review)
  done         → no dispatch

Each stage:
1. Receives issueId from dispatcher
2. Fetches issue + comments via GraphQL
3. Calls LLM (OpenAI-compatible /v1/chat/completions) with stage-specific prompt
4. Writes exactly one Linear comment and, if appropriate, one label/state change
5. Returns 200 to dispatcher fast

LLM Backend
-----------
Default: http://127.0.0.1:8642/v1 (Hermes API)
Swappable: any OpenAI-compatible endpoint. Set BACKEND_URL + BACKEND_KEY per stage.

Prompt Strategy
---------------
No memory. Each stage prompt is a static soul.md block appended to the system
message. The only context that travels between stages is the Linear issue
history and comments.

Router prompt: triage rules + output schema (ready | blocked + exactly one question)
Planner prompt: decomposition rules + infra decision tree
Executor prompt: implementation rules + sandbox execution rules
Critic prompt: review rules + security/dep scan rules + gate conditions

Permissions
-----------
Stage     Linear          GitHub          Shell/Sandbox
--------  --------------  --------------  -------------
dispatcher read webhook    none            none
router    read/write      contents:read   none
planner   read/write      repo:write      none
executor  read/write      repo:write      yes (sandboxed)
critic    read            read + write PR review/review-comments  optional

Infra
-----
- One directory per service under ~/linear-pipeline-prototype/
- Each has its own venv, .env, systemd user unit
- Shared Linear + GitHub client library at ./lib/
- Minimal deps: fastapi, uvicorn, httpx, pydantic-settings

Failure Model
-------------
- Dispatcher: 5XX on upstream crash → Linear retries → idempotent handler
- Stage crash: dispatcher gets 502, posts a comment "stage failed, retrying",
  and Linear webhook retry queue delivers it
- No stage mutates another stage's state; Linear state is the source of truth

Deployment Order
----------------
1. Shared lib (Linear/GraphQL client helpers)
2. dispatcher (routes but stages still stubs)
3. router (wire real prompt + tests)
4. planner
5. executor
6. critic
7. End-to-end smoke test on 5 issues
