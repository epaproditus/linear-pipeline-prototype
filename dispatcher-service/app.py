"""Dispatcher service: routes Linear webhook events to pipeline stages."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from pydantic_settings import BaseSettings

from lib.linear_client import LinearClient

log = logging.getLogger("dispatcher")

ROUTER_URL = os.getenv("ROUTER_URL", "http://127.0.0.1:8661/triage")
PLANNER_URL = os.getenv("PLANNER_URL", "http://127.0.0.1:8663/plan")
EXECUTOR_URL = os.getenv("EXECUTOR_URL", "http://127.0.0.1:8664/execute")
CRITIC_URL = os.getenv("CRITIC_URL", "http://127.0.0.1:8665/review")

STATE_ROUTES: dict[str, str] = {
    "needs-triage": ROUTER_URL,
    "ready": PLANNER_URL,
    "planned": EXECUTOR_URL,
    "in-review": CRITIC_URL,
}

class DispatcherSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
    allowed_team_ids: str = ""
    linear_api_key: str = ""
    webhook_secret: str = ""

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}

settings = DispatcherSettings()
app = FastAPI(title="pipeline-dispatcher")

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dispatcher"}

@app.post("/webhook")
async def webhook(request: Request) -> Response:
    body = await request.body()
    try:
        event = await request.json()
    except Exception:
        return Response(status_code=400)

    issue = (((event.get("issue") or {}).get("issue") or {}))
    issue_id = issue.get("id")
    issue_state_name = (((issue.get("state") or {}).get("name") or "")).lower()
    team_id = (((issue.get("team") or {}).get("id") or "")).lower()

    if not issue_id or not issue_state_name:
        return Response(status_code=202)

    url = STATE_ROUTES.get(issue_state_name)
    if not url:
        return Response(status_code=202)

    async with httpx.AsyncClient(timeout=httpx.Timeout(10, 60)) as client:
        resp = await client.post(url, json={"issue_id": issue_id})
    return Response(status_code=resp.status_code, content=resp.content, media_type="application/json")
