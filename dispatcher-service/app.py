"""Dispatcher service: routes Linear webhook events to pipeline stages."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from pydantic_settings import BaseSettings

from lib.linear_client import LinearClient

log = logging.getLogger("dispatcher")


class DispatcherSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
    allowed_team_ids: str = ""
    linear_api_key: str = ""
    webhook_secret: str = ""
    router_url: str = "http://127.0.0.1:8661/triage"
    planner_url: str = "http://127.0.0.1:8663/plan"
    executor_url: str = "http://127.0.0.1:8664/execute"
    critic_url: str = "http://127.0.0.1:8665/review"

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


settings = DispatcherSettings()
app = FastAPI(title="pipeline-dispatcher")

STATE_ROUTES: dict[str, str] = {
    "needs-triage": settings.router_url,
    "ready": settings.planner_url,
    "planned": settings.executor_url,
    "in-review": settings.critic_url,
}


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """HMAC-SHA256 verification of Linear webhook payload."""
    if not settings.webhook_secret:
        return True  # No secret configured — skip verification
    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dispatcher"}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    body = await request.body()

    # Verify HMAC signature
    sig = request.headers.get("Linear-Signature", "")
    if sig and not _verify_signature(body, sig):
        log.warning("Invalid webhook signature")
        return Response(status_code=401)

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

    # Only process the Playground team
    team_ids = settings.team_id_set
    if team_ids and team_id not in team_ids:
        return Response(status_code=202)

    url = STATE_ROUTES.get(issue_state_name)
    if not url:
        return Response(status_code=202)

    log.info("Routing %s (state=%s) → %s", issue_id, issue_state_name, url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(read=180.0)) as client:
        resp = await client.post(url, json={"issue_id": issue_id})
    return Response(status_code=resp.status_code, content=resp.content, media_type="application/json")
