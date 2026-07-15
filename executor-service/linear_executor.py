"""Executor-service FastAPI stub."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from lib.linear_client import LinearClient
from lib.backend import chat

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
app = FastAPI(title="executor-service")
linear = LinearClient(settings.linear_api_key)

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
    except Exception:
        log.exception("Executor comment failed %s", req.issue_id)

    return ExecDecision(summary=comment)
