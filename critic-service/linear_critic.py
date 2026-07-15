"""Critic-service FastAPI stub."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("critic-service")

CRITIC_SOUL = """\
You are the Critic stage of a Linear agent pipeline.
You receive issues entering `in-review`.
Your job: review the diff against the plan/AC, scan for high-severity findings,
and approve or request changes. Do not merge code directly.

Output contract:
- Approve: ONE comment starting with "LGTM:" plus findings summary or "None".
- Request changes: ONE comment starting with "Changes:" plus bullet findings.
Do not approve with unresolved high-severity findings.
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

# Export backend settings before importing backend
os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="critic-service")
linear = LinearClient(settings.linear_api_key)

# Pipeline state IDs — lazy-loaded
_CRITIC_STATES: dict[str, str] | None = None

def _load_state_ids() -> dict[str, str]:
    global _CRITIC_STATES
    if _CRITIC_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _CRITIC_STATES = {s["name"].lower(): s["id"] for s in states}
    return _CRITIC_STATES

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "critic"}

@app.post("/review")
def review(req: ReviewRequest) -> ReviewDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    prompt = f"{CRITIC_SOUL}\n\nIssue: {issue['identifier']}\nTitle: {issue['title']}\nDescription:\n{issue['description'] or ''}\n"
    answer = chat([{"role": "user", "content": prompt}])
    decision = "approve" if answer.startswith("LGTM:") else "changes"
    try:
        linear.create_comment(req.issue_id, answer)
        # Transition: approve → Done, changes → Planned
        states = _load_state_ids()
        target = "done" if decision == "approve" else "planned"
        target_id = states.get(target)
        if target_id:
            linear.update_issue_state(req.issue_id, target_id)
    except Exception:
        log.exception("Critic write failed %s", req.issue_id)
    return ReviewDecision(decision=decision, comment=answer)
