"""Planner-service FastAPI app."""

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

log = logging.getLogger("planner-service")

PLANNER_SOUL = """\
You are the Planner stage of a Linear agent pipeline.
You receive issues after the Router has marked them `ready`.

Your job: decompose the issue into ordered implementation steps.

OUTPUT JSON — your entire response must be valid JSON with NO extra text:
{
  "plan": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
  "infra_needed": false,
  "infra_notes": "optional description if infra is needed",
  "estimated_steps": 3
}

Do NOT output anything outside the JSON. No preamble, no markdown, no code fences.
"""


class PlannerSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
    backend_url: str = "http://127.0.0.1:8642/v1"
    backend_key: str = ""
    model: str = "hermes-agent"
    workdir: str = str(Path.home() / "linear-pipeline-prototype" / "planner-service" / "workspace")

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


class PlanRequest(BaseModel):
    issue_id: str

class PlanDecision(BaseModel):
    summary: str

settings = PlannerSettings()
assert settings.linear_api_key, "Planner requires LINEAR_API_KEY in .env"

os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="planner-service")
linear = LinearClient(settings.linear_api_key)
os.environ.pop("LINEAR_API_KEY", None)

_PLANNER_STATES: dict[str, str] | None = None

def _load_state_ids() -> dict[str, str]:
    global _PLANNER_STATES
    if _PLANNER_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _PLANNER_STATES = {s["name"].lower(): s["id"] for s in states}
    return _PLANNER_STATES

def _format_previous_stages(issue: dict[str, Any]) -> str:
    comments = issue.get("comments", {}).get("nodes", [])
    if not comments:
        return "(no prior stage output)"
    lines = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if body:
            excerpt = body if len(body) < 800 else body[:800] + "\n...(truncated)"
            lines.append(excerpt)
    return "\n\n---\n\n".join(lines)


@app.get("/health")
def health():
    return {"status": "ok", "service": "planner"}

@app.post("/plan")
def plan(req: PlanRequest) -> PlanDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    prior_context = _format_previous_stages(issue)
    identifier = issue["identifier"]
    title = issue["title"]
    description = issue.get("description") or ""

    prompt = (
        f"{PLANNER_SOUL}\n\n"
        f"Issue: {identifier} — {title}\n"
        f"Description: {description}\n"
        f"\n"
        f"Prior stage output:\n{prior_context}\n"
    )

    raw = chat([{"role": "user", "content": prompt}])

    # Parse JSON
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r'\{.*"plan".*\}', raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                parsed = {"plan": ["Error parsing plan from LLM"], "infra_needed": False}
        else:
            parsed = {"plan": ["Error: could not parse LLM output"], "infra_needed": False}

    comment = json.dumps(parsed, indent=2)

    try:
        linear.create_comment(req.issue_id, comment)
        states = _load_state_ids()
        planned_id = states.get("planned")
        if planned_id:
            linear.update_issue_state(req.issue_id, planned_id)
    except Exception:
        log.exception("Planner write failed %s", req.issue_id)

    return PlanDecision(summary=comment)
