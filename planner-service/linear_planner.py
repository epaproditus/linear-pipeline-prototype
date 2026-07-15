"""Planner-service FastAPI app."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from lib.linear_client import LinearClient
from lib.backend import chat

log = logging.getLogger("planner-service")

PLANNER_SOUL = """\
You are the Planner stage of a Linear agent pipeline.
You receive issues labeled `ready`.
Your job is to decompose the issue into ordered implementation steps and decide
whether new infra is required.

Output contract (single Linear comment, then update labels/state):
1. Begin with "Plan:" followed by an ordered markdown checklist of steps.
2. End with an "Infra:" section only if new repos/branches/services are needed.
3. When done, label the issue `planned` and add a summary comment.
"""

class PlannerSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
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
app = FastAPI(title="planner-service")
linear = LinearClient(settings.linear_api_key)


@app.get("/health")
def health():
    return {"status": "ok", "service": "planner"}


@app.post("/plan")
def plan(req: PlanRequest) -> PlanDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    prompt = f"{PLANNER_SOUL}\n\nIssue: {issue['identifier']}\nTitle: {issue['title']}\nDescription:\n{issue['description'] or ''}\n"
    answer = chat([{"role": "user", "content": prompt}])
    comment = answer or "Planned: no output."

    try:
        linear.create_comment(req.issue_id, comment)
    except Exception:
        log.exception("Planner comment failed %s", req.issue_id)

    return PlanDecision(summary=comment)
