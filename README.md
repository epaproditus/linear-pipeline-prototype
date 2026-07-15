# Linear Agent Pipeline Prototype

Multistage agent pipeline that processes Linear issues autonomously through
triage, planning, implementation, and review — driven by Linear state
transitions.

## Architecture

```
                     ┌──────────────────┐
                     │    Dispatcher     │  ←─ Linear webhook events
                     │     (port 8666)   │
                     └────────┬─────────┘
                              │ routes by issue.state.name
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
   ┌────────────┐      ┌───────────┐       ┌────────────┐
   │  Router    │      │  Planner  │       │  Critic    │
   │ :8670      │      │ :8663     │       │ :8665      │
   │ triage     │──►   │ planning  │       │ review     │
   │ ready/     │      │ checklist │       │ LGTM/      │
   │ blocked    │      │ + infra   │       │ Changes    │
   └────────────┘      └─────┬─────┘       └────────────┘
                              │
                              ▼
                       ┌────────────┐
                       │  Executor  │
                       │ :8664      │
                       │ implement  │
                       │ + PR       │
                       └────────────┘
```

Stages are connected by Linear state transitions, not direct RPC. The
Dispatcher receives a webhook event, reads the issue's current state, and
forwards the `issue_id` to the appropriate stage service. Each stage reads
the full issue from Linear via GraphQL, processes it with an LLM prompt,
writes a comment, and transitions the issue state — which triggers the next
webhook.

## Services

### Dispatcher (port 8666)

Central webhook receiver. Listens for Linear issue updates and routes each
event to the correct pipeline stage based on the issue's workflow state.

- `POST /webhook` — accepts Linear webhook payload, verifies HMAC-SHA256
  signature, looks up `issue.state.name` in the route table, and forwards
  `{"issue_id": "..."}` to the target stage.
- Route table: `needs-triage` → Router, `ready` → Planner, `planned` →
  Executor, `in-review` → Critic. Unknown states return 202 (ignored).
- Source: `dispatcher-service/app.py`

### Router (port 8670)

First pipeline stage. Receives issues entering `needs-triage` or `blocked`.
Validates whether an issue is ready for work.

- `POST /triage` — checks repo accessibility, acceptance criteria presence,
  and bounded scope via LLM. Outputs `Ready:` (pass) or `Blocked:` (fail
  with one clarifying question).
- On pass: transitions issue to `ready` state. On fail: transitions to
  `blocked`.
- Source: `router-service/linear_router.py`

### Planner (port 8663)

Second stage. Receives issues in `ready` state and decomposes them into
ordered implementation steps.

- `POST /plan` — analyses the issue description, generates a markdown
  checklist starting with `Plan:`, and optionally an `Infra:` section for
  new repos/branches/services.
- Transitions issue to `planned` state and adds the plan as a Linear
  comment.
- Source: `planner-service/linear_planner.py`

### Executor (port 8664)

Third stage. Receives issues in `planned` state and implements the plan
using Hermes Agent with full tool access (filesystem, shell, git, web).

- `POST /execute` — fetches the plan from the Planner's comment, then
  calls the Hermes API with `stream_tool_progress=True` for tool-enabled
  execution. The agent clones repos, makes changes, runs tests, commits,
  pushes, and opens a GitHub PR.
- Transitions issue to `in-review` state on completion.
- Source: `executor-service/linear_executor.py`

### Critic (port 8665)

Fourth and final stage. Receives issues in `in-review` state and reviews
the implementation against the plan and acceptance criteria.

- `POST /review` — inspects the issue and its comments for the plan and any
  execution summary. Outputs `LGTM:` (approve) or `Changes:` (request
  changes with bullet findings).
- On approve: transitions issue to `done`. On changes requested: transitions
  back to `planned` for rework.
- Source: `critic-service/linear_critic.py`

## Port Map

| Service    | Port | Endpoint(s)              |
|------------|------|--------------------------|
| Dispatcher | 8666 | GET /health, POST /webhook |
| Router     | 8670 | GET /health, POST /triage  |
| Planner    | 8663 | GET /health, POST /plan    |
| Executor   | 8664 | GET /health, POST /execute |
| Critic     | 8665 | GET /health, POST /review  |

## State Flow

```
needs-triage  ──►  Router  ──►  ready  ──►  Planner  ──►  planned  ──►  Executor
      │                                                                        │
      └──►  blocked  ◄─────────────────────────────────────────────────────────┘
                                                                               │
                                                        ┌──────────────────────┘
                                                        ▼
                                                   in-review  ──►  Critic
                                                                     │
                                              ┌──────────────────────┼──────┐
                                              ▼                      │      ▼
                                             done                   │  planned
                                                                    │  (rework)
                                                                    ▼
                                                              Executor
```

## Setup

### Prerequisites

- Python 3.12+
- `uv` (recommended) or `pip`
- Linear API key with issue read/write permissions
- Hermes API backend (default: `http://127.0.0.1:8642/v1`) or any
  OpenAI-compatible endpoint

### Quick Start

```bash
# Clone the repo
git clone git@github.com:epaproditus/linear-pipeline-prototype.git
cd linear-pipeline-prototype

# Install shared dependencies
pip install -r requirements.in
```

Each service runs in its own directory with its own virtual environment and
configuration. Navigate to a service directory, create its `.env` from the
example template, install deps, and start it:

```bash
cd router-service
cp .env.example .env
# Edit .env with your LINEAR_API_KEY and ALLOWED_TEAM_IDS
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.in
./run.sh
```

Repeat for each service (planner, executor, critic, dispatcher).

### Environment Variables

Each service reads from its own `.env` file:

| Variable         | Description                                   | Default                           |
|------------------|-----------------------------------------------|-----------------------------------|
| LINEAR_API_KEY   | Linear API key (pat or OAuth)                 | (required)                        |
| ALLOWED_TEAM_IDS | Comma-separated Linear team IDs to process    | (required)                        |
| BACKEND_URL      | LLM backend OpenAI-compatible endpoint        | http://127.0.0.1:8642/v1          |
| BACKEND_KEY      | Optional bearer token for backend             | (empty)                           |
| MODEL            | Model name to use via backend                 | hermes-agent                      |
| WEBHOOK_SECRET   | (dispatcher only) HMAC secret for webhook sig | (empty = skip verification)       |

### Running All Services

Each service includes a `run.sh` script that loads `.env`, activates the
venv, and starts a uvicorn server. Start them in separate terminals or as
systemd user units:

```bash
# Per-service (from service directory)
./run.sh
```

The Dispatcher must be reachable by Linear (via webhook URL or Cloudflare
Tunnel). Configure a Linear webhook in your team settings pointing to
`https://your-domain.com/webhook`.

## Shared Library

The `lib/` directory contains two modules used by all services:

- **`linear_client.py`** — GraphQL client for the Linear API. Provides
  `get_issue()`, `create_comment()`, `get_team_states()`, and
  `update_issue_state()`.
- **`backend.py`** — OpenAI-compatible LLM backend client. Two modes:
  `chat()` for simple completion (Router, Planner, Critic) and
  `agent_chat()` for streaming tool-enabled execution (Executor only).

## Dependencies

```
fastapi        — HTTP framework
uvicorn        — ASGI server
httpx          — HTTP client (Linear GraphQL + backend LLM calls)
pydantic       — Data validation
pydantic-settings  — .env config loading
```

Defined in `requirements.in`.
