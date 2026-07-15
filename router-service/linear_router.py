"""Router-service FastAPI app."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("router-service")

ROUTER_SOUL = """\
You are the Router stage of a Linear agent pipeline.
You receive issues entering `needs-triage` or `blocked`.
Your job is bounded triage only. Do not implement, plan, or debug.

Checks for ready:
1. Repo exists and is accessible. The repo URL may be in the issue
   description, labels, or project context.
2. Acceptance criteria are present. If the issue has no explicit AC, treat that
   as a blocker unless the description itself contains measurable done-criteria.
3. Scope is bounded. Reject open-ended requests like "rewrite the auth system"
   without an explicit files/components list.

Output contract (strict):
- Pass: respond with ONLY this text:
    Ready: <one-line rationale>
- Fail: respond with ONLY this text:
    Blocked: <one-line reason>. Question: <exactly one clarifying question>
Do not post multiple questions or paragraphs of suggestions.
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

# Export backend settings BEFORE importing backend (so BackendSettings() sees them)
os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="router-service")
linear = LinearClient(settings.linear_api_key)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "router"}

@app.post("/triage")
def triage(req: TriageRequest) -> TriageDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        return TriageDecision(status="blocked", comment="Blocked: not in allowed team.", labels=["blocked"])

    messages = [
        {"role": "system", "content": ROUTER_SOUL},
        {"role": "user", "content": f"Issue: {issue['identifier']}\nTitle: {issue['title']}\nDescription:\n{issue['description'] or ''}\nState: {issue['state']['name']}"},
    ]
    raw = chat(messages)
    status = "ready" if raw.startswith("Ready:") else "blocked"
    decision = TriageDecision(status=status, comment=raw, labels=[status])
    try:
        linear.create_comment(req.issue_id, raw)
    except Exception:
        log.exception("Linear write failed %s", req.issue_id)
    return decision
