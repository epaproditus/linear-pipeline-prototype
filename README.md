# Linear Pipeline Prototype

Four FastAPI services coordinated by Linear state transitions.

Services
- dispatcher:8660 — receives Linear webhook, routes by `issue.state.name`
- router:8661 — needs-triage → ready/blocked + comment
- planner:8663 — ready → planned + plan comment
- executor:8664 — planned → in-review + PR
- critic:8665 — in-review → approve/request changes

Usage
- Copy service `.env.example` to `.env` per service.
- Install deps: `pip install -r requirements.in`
- Run each service: `./run.sh` or via `systemctl --user start <service>`
