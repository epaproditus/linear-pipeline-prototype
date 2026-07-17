"""Executor-service FastAPI app — calls Hermes API with full tool access."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("executor-service")

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

os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import agent_chat

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


def _fetch_plan_comment(issue: dict[str, Any]) -> str:
    """Extract the plan from issue comments (posted by Planner)."""
    comments = issue.get("comments", {}).get("nodes", [])
    for c in reversed(comments):
        body = c.get("body", "") or ""
        if body.strip().startswith("Plan:"):
            return body
    return ""


# Patterns indicating a repeat credential blocker (not a code problem)
_REPEAT_BLOCKER_PATTERNS = [
    "401",
    "unauthorized",
    "sbp_e72fca",
    "dead PAT",
    "edge function returned",
    "couldn't load model settings",
    "non-2xx status code",
    "supabase access token",
    "management api",
]


def _count_prior_blocker_hits(issue: dict[str, Any]) -> int:
    """Count distinct prior comments that mention the same credential blocker."""
    comments = issue.get("comments", {}).get("nodes", [])
    hits = 0
    for c in comments:
        body = c.get("body", "") or ""
        lower = body.lower()
        for pattern in _REPEAT_BLOCKER_PATTERNS:
            if pattern in lower:
                hits += 1
                break  # one hit per comment
    return hits


# Track issues that hit the retry cap so we don't re-check every time
_RETRY_CAPPED: set[str] = set()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "executor"}


@app.post("/execute")
def execute(req: ExecRequest) -> ExecDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    identifier = issue["identifier"]

    # ── Retry capping: detect repeated same-blocker loop ──────────────
    prior_hits = _count_prior_blocker_hits(issue)
    if prior_hits >= 3 or identifier in _RETRY_CAPPED:
        _RETRY_CAPPED.add(identifier)
        log.warning(
            "Retry cap: %s has %d+ blocker comments — moving to Blocked",
            identifier, prior_hits,
        )
        states = _load_state_ids()
        blocked_id = states.get("blocked")
        if blocked_id:
            try:
                linear.update_issue_state(req.issue_id, blocked_id)
            except Exception:
                log.exception("Failed to move %s to Blocked", identifier)
        comment = (
            "**Skipped: repeated credential blocker**\n\n"
            f"This issue has been attempted {prior_hits}+ times with the same credential error. "
            "The executor will not re-run — a human needs to fix the credential (e.g. rotate "
            "the Supabase PAT) and re-add the `autopilot` label."
        )
        try:
            linear.create_comment(req.issue_id, comment)
        except Exception:
            log.exception("Failed to post retry-cap comment")
        return ExecDecision(summary=comment)

    # ── Build prompt ──────────────────────────────────────────────────
    title = issue["title"]
    description = issue.get("description") or ""
    plan = _fetch_plan_comment(issue)

    prompt = (
        f"You are Hermes, an autonomous implementation agent.\n"
        f"Tools (filesystem, shell, git, web) are available.\n"
        f"\n"
        f"Issue: {identifier} — {title}\n"
        f"Description: {description}\n"
        f"\n"
        f"Plan from previous stage:\n{plan or '(no plan found)'}\n"
        f"\n"
        f"Implement the plan. Clone the repo, make changes, run tests,\n"
        f"commit, push, and open a GitHub PR. Reference {identifier}.\n"
        f"\n"
        f"ABSOLUTE PROHIBITIONS — violations will be auto-rejected:\n"
        f"1. Do NOT use the browser tool to visit login pages, password-reset flows,\n"
        f"   credential dashboards, or authentication pages of any kind.\n"
        f"2. Do NOT attempt to recover, reset, create, or bypass credentials or tokens.\n"
        f"3. Do NOT try passwords, login forms, or OAuth flows.\n"
        f"4. Do NOT interact with the Linear API, post comments on issues, or change\n"
        f"   issue state — the pipeline handles all Linear updates.\n"
        f"5. If you hit an auth/credential error, stop and report it in your summary;\n"
        f"   do NOT try to work around it or find alternative credentials.\n"
        f"\n"
        f"Use your tools to implement the fix. When done, output a JSON summary as\n"
        f"your FINAL message (no extra text after it):\n"
        f'{{"summary":"what was done","pr_url":"https://...","changes":["file1","file2"]}}\n'
    )

    # Strip the Linear API key so agent shell tools can never discover it
    os.environ.pop("LINEAR_API_KEY", None)
    # Also strip any Supabase tokens that may be in the environment
    os.environ.pop("SUPABASE_ACCESS_TOKEN", None)
    os.environ.pop("SUPABASE_PAT", None)

    answer = agent_chat([{"role": "user", "content": prompt}])

    # Parse the JSON summary if present; fall back to whole answer
    import json
    import re
    try:
        parsed = json.loads(answer)
        summary = parsed.get("summary", answer)
        log.info("Executor %s parsed JSON summary: %s", identifier, summary[:200])
    except (json.JSONDecodeError, TypeError):
        m = re.search(r'\{"summary":.*"changes":\[.*\]\}', answer, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
                summary = parsed.get("summary", answer)
            except (json.JSONDecodeError, TypeError):
                summary = answer
        else:
            summary = answer

    # Post comment (by the pipeline service, NOT by the agent)
    try:
        linear.create_comment(req.issue_id, summary)
        states = _load_state_ids()
        in_review_id = states.get("in review")
        if in_review_id:
            linear.update_issue_state(req.issue_id, in_review_id)
    except Exception:
        log.exception("Executor write failed %s", req.issue_id)

    return ExecDecision(summary=summary)
