"""Tests for dispatcher-service/app.py.

Tests both the GitHub repository_dispatch integration and the webhook handler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient

# Set env before importing app so settings get test values
os.environ["LINEAR_API_KEY"] = "test-key"
os.environ["ALLOWED_TEAM_IDS"] = "team-1"
os.environ["GITHUB_TOKEN"] = "gh-test-token"
os.environ["WEBHOOK_SECRET"] = "test-secret"

from app import app, DISPATCH_STATES, GITHUB_REPO, GITHUB_API, DISPATCH_URL  # noqa: E402

client = TestClient(app)

TEAM_ID = "team-1"
IDENTIFIER = "PLY-298"


def _sign(body: bytes) -> str:
    """Sign a body with the test webhook secret."""
    return hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def _make_webhook(
    action: str = "update",
    issue_id: str = "iss-123",
    state_name: str = "needs-triage",
    team_id: str = TEAM_ID,
    identifier: str = IDENTIFIER,
) -> dict:
    return {
        "action": action,
        "type": "Issue",
        "data": {
            "id": issue_id,
            "identifier": identifier,
            "state": {"name": state_name},
            "team": {"id": team_id},
        },
    }


class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["service"] == "dispatcher"


class TestWebhookParsing:
    def test_missing_body_returns_400(self):
        resp = client.post("/webhook", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_invalid_signature_returns_401(self):
        payload = _make_webhook()
        resp = client.post(
            "/webhook",
            json=payload,
            headers={"Linear-Signature": "deadbeef"},
        )
        assert resp.status_code == 401

    def test_valid_signature_succeeds(self):
        payload = _make_webhook(state_name="needs-triage")
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Linear-Signature": sig,
            },
        )
        # 202 means accepted (dispatched in background)
        assert resp.status_code == 202

    def test_no_signature_still_works_when_secret_set(self):
        """With a secret set, missing signature should reject."""
        payload = _make_webhook(state_name="needs-triage")
        resp = client.post("/webhook", json=payload)
        # No signature header with a secret set → 401
        assert resp.status_code == 401


class TestStateRouting:
    @pytest.mark.parametrize("state", ["needs-triage", "ready", "planned", "in-review"])
    def test_dispatch_states_get_202(self, state):
        payload = _make_webhook(state_name=state)
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "Linear-Signature": sig},
        )
        assert resp.status_code == 202, f"{state} should dispatch"

    @pytest.mark.parametrize("state", ["in progress", "done", "blocked", "backlog", "triage", ""])
    def test_non_dispatch_states_get_202_but_skipped(self, state):
        payload = _make_webhook(state_name=state)
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "Linear-Signature": sig},
        )
        assert resp.status_code == 202, f"{state} should be skipped but still return 202"

    def test_missing_issue_id_returns_202_skipped(self):
        payload = _make_webhook()
        payload["data"] = {"state": {"name": "needs-triage"}, "team": {"id": TEAM_ID}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "Linear-Signature": sig},
        )
        assert resp.status_code == 202

    def test_unknown_team_is_skipped(self):
        payload = _make_webhook(team_id="wrong-team")
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "Linear-Signature": sig},
        )
        assert resp.status_code == 202


class TestDispatchStates:
    def test_dispatch_states_set(self):
        """Verify the exact set of states that trigger GitHub dispatch."""
        assert DISPATCH_STATES == {"needs-triage", "ready", "planned", "in-review"}
        assert len(DISPATCH_STATES) == 4

    def test_dispatch_url(self):
        """Verify the GitHub API endpoint URL."""
        assert DISPATCH_URL == f"{GITHUB_API}/repos/{GITHUB_REPO}/dispatches"
        assert GITHUB_REPO == "epaproditus/linear-pipeline-prototype"


class TestOldStageUrlsRemoved:
    """AC #6: Old stage URLs must be removed — no forwarding to internal services."""

    def test_no_stage_url_fields_in_settings(self):
        """Settings should not contain stage URL declared fields."""
        from app import settings  # noqa: F402
        # Check declared model fields (not dynamic extra fields from .env)
        model_fields = type(settings).model_fields
        assert "router_url" not in model_fields, "router_url should not be a declared field"
        assert "planner_url" not in model_fields, "planner_url should not be a declared field"
        assert "executor_url" not in model_fields, "executor_url should not be a declared field"
        assert "critic_url" not in model_fields, "critic_url should not be a declared field"

    def test_no_state_routes_dict(self):
        """STATE_ROUTES should not exist anywhere in the module."""
        import app as app_module
        assert not hasattr(app_module, "STATE_ROUTES"), "STATE_ROUTES should be removed"
