"""Critic-service FastAPI stub."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("critic-service")

CRITIC_SOUL = """\
You are the Critic stage of a Linear agent pipeline.
You receive issues entering `In Review`.

Your job: review the implementation against the acceptance criteria and plan.
Read the full thread — the Router checked readiness, the Planner made a plan,
and the Executor implemented it. Verify the implementation meets the AC.

OUTPUT JSON — your entire response must be valid JSON with NO extra text:
{
  "decision": "approve" or "changes",
  "findings": "summary of what was reviewed and the outcome",
  "ac_met": true/false
}

If requesting changes, include:
  "required_changes": ["change 1", "change 2"]

Do NOT output anything outside the JSON. No preamble, no markdown, no code fences.
"""


class CriticSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
    backend_url: str = "http://127.0.0.1:8642/v1"
    backend_key: str = ""
    model: str = "hermes-agent"
    workdir: str = str(Path.home() / "linear-pipeline-prototype" / "critic-service" / "workspace")

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


class ReviewRequest(BaseModel):
    issue_id: str

class ReviewDecision(BaseModel):
    decision: str
    comment: str

settings = CriticSettings()
assert settings.linear_api_key, "Critic requires LINEAR_API_KEY in .env"

os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="critic-service")
linear = LinearClient(settings.linear_api_key)
os.environ.pop("LINEAR_API_KEY", None)

_CRITIC_STATES: dict[str, str] | None = None

def _load_state_ids() -> dict[str, str]:
    global _CRITIC_STATES
    if _CRITIC_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _CRITIC_STATES = {s["name"].lower(): s["id"] for s in states}
    return _CRITIC_STATES

def _format_previous_stages(issue: dict[str, Any]) -> str:
    comments = issue.get("comments", {}).get("nodes", [])
    if not comments:
        return "(no prior stage output)"
    lines = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if body:
            excerpt = body if len(body) < 1000 else body[:1000] + "\n...(truncated)"
            lines.append(excerpt)
    return "\n\n---\n\n".join(lines)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "critic"}

@app.post("/review")
def review(req: ReviewRequest) -> ReviewDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    # Guard: only process if still In Review (prevents duplicate trigger
    # when Critic's own state transition fires a webhook back here)
    current_state = issue["state"]["name"].lower()
    if current_state not in ("in review",):
        log.info("Skipped: issue %s is '%s', not 'in review'", req.issue_id[:8], current_state)
        return ReviewDecision(decision="skip", comment='{"decision":"skip","reason":"already processed"}')

    prior_context = _format_previous_stages(issue)
    identifier = issue["identifier"]
    title = issue["title"]
    description = issue.get("description") or ""

    prompt = (
        f"{CRITIC_SOUL}\n\n"
        f"Issue: {identifier} — {title}\n"
        f"Description: {description}\n"
        f"\n"
        f"Full thread from all stages:\n{prior_context}\n"
    )

    raw = chat([{"role": "user", "content": prompt}])

    # Parse JSON
    try:
        parsed = json.loads(raw)
        decision = parsed.get("decision", "changes")
        if decision not in ("approve", "changes"):
            decision = "changes"
    except (json.JSONDecodeError, TypeError):
        m = re.search(r'\{.*"decision".*\}', raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
                decision = parsed.get("decision", "changes")
            except json.JSONDecodeError:
                decision = "changes"
            parsed = {"decision": decision, "findings": "parse fallback"}
        else:
            decision = "changes"
            parsed = {"decision": decision, "findings": "parse fallback"}

    comment = json.dumps(parsed, indent=2)

    try:
        linear.create_comment(req.issue_id, comment)
        states = _load_state_ids()
        target = "done" if decision == "approve" else "planned"
        target_id = states.get(target)
        if target_id:
            linear.update_issue_state(req.issue_id, target_id)
    except Exception:
        log.exception("Critic write failed %s", req.issue_id)

    return ReviewDecision(decision=decision, comment=comment)
