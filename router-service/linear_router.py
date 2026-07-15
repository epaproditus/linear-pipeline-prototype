"""Router-service FastAPI app."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("router-service")

ROUTER_SOUL = """\
You are the Router stage of a Linear agent pipeline.
You receive issues entering `needs-triage` or `blocked`.
Your job is bounded triage only. Do not implement, plan, or debug.

Checks for ready:
1. Repo exists and is accessible.
2. Acceptance criteria are present.
3. Scope is bounded.

OUTPUT JSON — your entire response must be valid JSON with NO extra text:
{
  "status": "ready" or "blocked",
  "reason": "one-line rationale",
  "checks": {
    "repo_exists": true/false,
    "ac_present": true/false,
    "scope_bounded": true/false
  }
}

If blocked, also include:
  "question": "one clarifying question"

Do NOT output anything outside the JSON. No preamble, no markdown, no code fences.
"""


class RouterSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
    backend_url: str = "http://127.0.0.1:8642/v1"
    backend_key: str = ""
    model: str = "hermes-agent"
    workdir: str = str(Path.home() / "linear-pipeline-prototype" / "router-service" / "workspace")

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


class TriageRequest(BaseModel):
    issue_id: str

class TriageDecision(BaseModel):
    status: str
    comment: str
    labels: list[str] = []

settings = RouterSettings()
assert settings.linear_api_key, "Router requires LINEAR_API_KEY in .env"

os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="router-service")
linear = LinearClient(settings.linear_api_key)
os.environ.pop("LINEAR_API_KEY", None)

_ROUTER_STATES: dict[str, str] | None = None

def _load_state_ids() -> dict[str, str]:
    global _ROUTER_STATES
    if _ROUTER_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _ROUTER_STATES = {s["name"].lower(): s["id"] for s in states}
    return _ROUTER_STATES

def _format_previous_stages(issue: dict[str, Any]) -> str:
    """Build context string from previous stage comments."""
    comments = issue.get("comments", {}).get("nodes", [])
    if not comments:
        return "(no prior stage output)"
    lines = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if body:
            # Truncate very long comments for the prompt
            excerpt = body if len(body) < 800 else body[:800] + "\n...(truncated)"
            lines.append(excerpt)
    return "\n\n---\n\n".join(lines)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "router"}

@app.post("/triage")
def triage(req: TriageRequest) -> TriageDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        return TriageDecision(status="blocked", comment=json.dumps({"status":"blocked","reason":"not in allowed team"}), labels=["blocked"])

    prior_context = _format_previous_stages(issue)
    identifier = issue["identifier"]
    title = issue["title"]
    description = issue.get("description") or ""

    prompt = (
        f"{ROUTER_SOUL}\n\n"
        f"Issue: {identifier} — {title}\n"
        f"Description: {description}\n"
        f"State: {issue['state']['name']}\n"
        f"\n"
        f"Prior stage output:\n{prior_context}\n"
    )

    raw = chat([{"role": "user", "content": prompt}])

    # Parse JSON from response
    try:
        parsed = json.loads(raw)
        status = parsed.get("status", "blocked")
        if status not in ("ready", "blocked"):
            status = "blocked"
    except (json.JSONDecodeError, TypeError):
        # Fallback: accept raw JSON anywhere in the text
        log.warning("Router JSON parse failed, raw=%s", raw[:200])
        # Try to find JSON block
        import re as _re
        m = _re.search(r'\{.*"status".*\}', raw, _re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
                status = parsed.get("status", "blocked")
            except json.JSONDecodeError:
                status = "blocked"
        else:
            status = "blocked"
        parsed = {"status": status, "reason": "parse fallback", "checks": {}}

    comment = json.dumps(parsed, indent=2)
    decision = TriageDecision(status=status, comment=comment, labels=[status])

    try:
        linear.create_comment(req.issue_id, comment)
        states = _load_state_ids()
        target_state = states.get(status)
        if target_state:
            linear.update_issue_state(req.issue_id, target_state)
    except Exception:
        log.exception("Linear write failed %s", req.issue_id)

    return decision
