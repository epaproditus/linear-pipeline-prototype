"""Executor-service FastAPI stub."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("executor-service")

EXECUTOR_SOUL = """\
You are the Executor stage of a Linear agent pipeline.
You receive issues labeled `planned`.
Your job: implement the plan, run tests, open a PR, label `in-review`.
Output contract:
- Post ONE summary comment with PR URL + test results.
- Do not change unrelated issues.
"""

class ExecutorSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
    backend_url: str = "http://127.0.0.1:8642/v1"
    backend_key: str = ""
    model: str = "hermes-agent"
    workdir: str = str(Path.home() / "linear-pipeline-prototype" / "executor-service" / "workspace")

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


class ExecRequest(BaseModel):
    issue_id: str

class ExecDecision(BaseModel):
    summary: str

settings = ExecutorSettings()
assert settings.linear_api_key, "Executor requires LINEAR_API_KEY in .env"

# Export backend settings before importing backend
os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="executor-service")
linear = LinearClient(settings.linear_api_key)

# Pipeline state IDs — lazy-loaded
_EXECUTOR_STATES: dict[str, str] | None = None

def _load_state_ids() -> dict[str, str]:
    global _EXECUTOR_STATES
    if _EXECUTOR_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _EXECUTOR_STATES = {s["name"].lower(): s["id"] for s in states}
    return _EXECUTOR_STATES

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "executor"}

@app.post("/execute")
def execute(req: ExecRequest) -> ExecDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    prompt = f"{EXECUTOR_SOUL}\n\nIssue: {issue['identifier']}\nTitle: {issue['title']}\nDescription:\n{issue['description'] or ''}\n"
    answer = chat([{"role": "user", "content": prompt}])
    comment = answer or "Executed: no output."

    try:
        linear.create_comment(req.issue_id, comment)
        # Transition to In Review
        states = _load_state_ids()
        in_review_id = states.get("in review")
        if in_review_id:
            linear.update_issue_state(req.issue_id, in_review_id)
    except Exception:
        log.exception("Executor write failed %s", req.issue_id)

    return ExecDecision(summary=comment)
