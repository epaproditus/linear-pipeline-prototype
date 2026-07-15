"""Dispatcher service: routes Linear webhook events to GitHub repository_dispatch."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic_settings import BaseSettings

# Log to stderr so systemd/journald captures it
log = logging.getLogger("dispatcher")
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
))
log.addHandler(_handler)
log.setLevel(logging.INFO)

GITHUB_API = "https://api.github.com"
GITHUB_REPO = "epaproditus/linear-pipeline-prototype"
DISPATCH_URL = f"{GITHUB_API}/repos/{GITHUB_REPO}/dispatches"

# States that trigger a GitHub repository_dispatch event
DISPATCH_STATES = frozenset({
    "needs-triage",
    "ready",
    "planned",
    "in-review",
})


class DispatcherSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
    allowed_team_ids: str = ""
    linear_api_key: str = ""
    webhook_secret: str = ""
    github_token: str = ""


settings = DispatcherSettings()
app = FastAPI(title="pipeline-dispatcher")


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


async def fire_github_dispatch(issue_id: str, state: str, identifier: str = "") -> None:
    """POST a repository_dispatch event to GitHub.

    Only fires for states in DISPATCH_STATES; all others are silently skipped.
    """
    if state not in DISPATCH_STATES:
        log.info("Skipping dispatch for state '%s' (issue=%s)", state, identifier or issue_id)
        return

    if not settings.github_token:
        log.warning("GITHUB_TOKEN not set — skipping dispatch for %s", identifier or issue_id)
        return

    payload = {
        "event_type": "linear-issue",
        "client_payload": {
            "issue_id": issue_id,
            "state": state,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                DISPATCH_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.github_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "hermes-dispatcher/1.0",
                },
            )
        if resp.status_code in (200, 201, 204):
            log.info(
                "GitHub dispatch OK (%d) — issue=%s state=%s",
                resp.status_code,
                identifier or issue_id,
                state,
            )
        else:
            log.error(
                "GitHub dispatch FAILED (%d) — issue=%s state=%s body=%s",
                resp.status_code,
                identifier or issue_id,
                state,
                resp.text[:500],
            )
    except httpx.TimeoutException:
        log.error("GitHub dispatch TIMEOUT — issue=%s state=%s", identifier or issue_id, state)
    except httpx.RequestError as exc:
        log.error(
            "GitHub dispatch REQUEST ERROR (%s) — issue=%s state=%s",
            exc,
            identifier or issue_id,
            state,
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dispatcher"}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    body = await request.body()

    # Parse and log the event
    try:
        raw = json.loads(body)
        action = raw.get("action", "?")
        event_type = raw.get("type", "")
        log.info("Webhook: action=%s type=%s", action, event_type)
    except Exception:
        log.error("Failed to parse body: %s", body[:500])
        return Response(status_code=400)

    # Verify HMAC signature
    sig = request.headers.get("Linear-Signature", "")
    if settings.webhook_secret and not _verify_signature(body, sig):
        log.warning("Invalid webhook signature")
        return Response(status_code=401)

    # Linear webhook format: type="Issue", data = the issue object directly
    data = raw.get("data", {})
    issue_id = data.get("id")
    issue_ident = data.get("identifier", issue_id[:12] if issue_id else "?")
    issue_state_name = (data.get("state") or {}).get("name", "").lower()
    team_id = (data.get("team") or {}).get("id", "").lower()

    log.info("Event: issue=%s state='%s' team=%s", issue_ident, issue_state_name, team_id[:8])

    if not issue_id or not issue_state_name:
        log.info("Skipped: missing issue_id or state")
        return Response(status_code=202)

    # Only process allowed teams
    team_ids = {tid.strip() for tid in settings.allowed_team_ids.split(",") if tid.strip()}
    if team_ids and team_id not in team_ids:
        log.info("Skipped: team %s not in allowed set", team_id[:8])
        return Response(status_code=202)

    if issue_state_name not in DISPATCH_STATES:
        log.info("Skipped: state '%s' not in dispatch set", issue_state_name)
        return Response(status_code=202)

    log.info("Firing GitHub dispatch for %s (state=%s)", issue_ident, issue_state_name)
    background_tasks.add_task(
        fire_github_dispatch,
        issue_id,
        issue_state_name,
        issue_ident,
    )
    return Response(status_code=202)
