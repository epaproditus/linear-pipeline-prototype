"""Dispatcher service: routes Linear webhook events to pipeline stages."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic_settings import BaseSettings

log = logging.getLogger("dispatcher")


class DispatcherSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
    allowed_team_ids: str = ""
    linear_api_key: str = ""
    webhook_secret: str = ""
    router_url: str = "http://127.0.0.1:8670/triage"
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
        return True
    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _dispatch(url: str, issue_id: str, identifier: str) -> None:
    """Fire-and-forget forwarding to a pipeline stage."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            resp = await client.post(url, json={"issue_id": issue_id})
        log.info("Dispatched %s → %s (status=%s)", identifier, url, resp.status_code)
    except Exception:
        log.exception("Dispatch failed %s → %s", identifier, url)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dispatcher"}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    body = await request.body()

    # Log the raw event for debugging
    try:
        raw = json.loads(body)
        action = raw.get("action", "?")
        log.info("Webhook: action=%s type=%s", action, type(raw).__name__)
    except Exception:
        log.info("Webhook: body=%s", body[:200])
        return Response(status_code=400)

    # Verify HMAC signature
    sig = request.headers.get("Linear-Signature", "")
    if sig and not _verify_signature(body, sig):
        log.warning("Invalid webhook signature")
        return Response(status_code=401)

    # Support two payload shapes:
    #   Linear webhook:    data.issue.id
    #   Agent session:     issue.issue.id
    data = raw.get("data", {})
    raw_issue = data.get("issue") or raw.get("issue", {}).get("issue") or {}

    issue_id = raw_issue.get("id")
    issue_ident = raw_issue.get("identifier") or raw_issue.get("id", "?")[:12]
    issue_state_name = (raw_issue.get("state") or {}).get("name", "").lower()
    team_id = (raw_issue.get("team") or {}).get("id", "").lower()

    log.info(
        "Event: issue=%s state='%s' team=%s action=%s",
        issue_ident, issue_state_name, team_id[:8], action,
    )

    if not issue_id or not issue_state_name:
        return Response(status_code=202)

    # Only process allowed teams
    team_ids = settings.team_id_set
    if team_ids and team_id not in team_ids:
        log.info("Skipped: team not in allowed set")
        return Response(status_code=202)

    url = STATE_ROUTES.get(issue_state_name)
    if not url:
        log.info("Skipped: no route for state '%s'", issue_state_name)
        return Response(status_code=202)

    background_tasks.add_task(_dispatch, url, issue_id, issue_ident)
    return Response(status_code=202)
