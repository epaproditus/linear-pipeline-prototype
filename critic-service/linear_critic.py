"""Critic-service: reviews diffs, checks CI, and verifies deployed endpoints."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

log = logging.getLogger("critic-service")

CRITIC_SOUL = """\
You are the Critic stage of a Linear agent pipeline.
You receive issues entering `in-review`.
Your job: review the diff against the plan/AC, scan for high-severity findings,
and approve or request changes. Do not merge code directly.

Output a JSON object with these fields:
- "verdict": "approve" or "changes"
- "summary": one-sentence summary of your review
- "findings": list of strings, each a finding (use ["None"] if no issues found)

JSON only — no markdown, no extra text.
"""

GITHUB_PR_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")
GITHUB_API = "https://api.github.com"

# Match supabase edge function URLs and similar API endpoints
ENDPOINT_RE = re.compile(
    r"https://[a-z0-9]+\.supabase\.co/functions/v1/[a-zA-Z0-9_-]+"
)


class CriticSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    linear_api_key: str = ""
    allowed_team_ids: str = ""
    backend_url: str = "http://127.0.0.1:8642/v1"
    backend_key: str = ""
    model: str = "hermes-agent"
    critic_auto_merge: bool = False
    github_token: str = ""
    workdir: str = str(Path.home() / "linear-pipeline-prototype" / "critic-service" / "workspace")

    @property
    def team_id_set(self) -> set[str]:
        return {tid.strip() for tid in self.allowed_team_ids.split(",") if tid.strip()}


class ReviewRequest(BaseModel):
    issue_id: str


class ReviewDecision(BaseModel):
    decision: str
    comment: str


class CIResult(BaseModel):
    ci_found: bool = False
    ci_passing: bool = False
    ci_errors: list[str] = []
    ci_pending: bool = False


settings = CriticSettings()
assert settings.linear_api_key, "Critic requires LINEAR_API_KEY in .env"

# Export backend settings before importing backend
os.environ["BACKEND_URL"] = settings.backend_url
os.environ["BACKEND_KEY"] = settings.backend_key
os.environ["MODEL"] = settings.model

from lib.linear_client import LinearClient
from lib.backend import chat

app = FastAPI(title="critic-service")
linear = LinearClient(settings.linear_api_key)

# Pipeline state IDs — lazy-loaded
_CRITIC_STATES: dict[str, str] | None = None


def _load_state_ids() -> dict[str, str]:
    global _CRITIC_STATES
    if _CRITIC_STATES is None:
        team_id = list(settings.team_id_set)[0]
        states = linear.get_team_states(team_id)
        _CRITIC_STATES = {s["name"].lower(): s["id"] for s in states}
    return _CRITIC_STATES


def _extract_pr_url(comments: list[dict[str, Any]]) -> str | None:
    """Scan issue comments for a GitHub PR URL."""
    for comment in comments:
        body = comment.get("body", "") or ""
        match = GITHUB_PR_RE.search(body)
        if match:
            owner, repo, pr_num = match.group(1), match.group(2), match.group(3)
            return f"https://github.com/{owner}/{repo}/pull/{pr_num}"
    return None


def _get_latest_commit_sha(owner: str, repo: str, pr_num: int) -> str | None:
    """Get the latest commit SHA for a PR using the GitHub API."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_num}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "linear-pipeline-critic/1.0",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return data.get("head", {}).get("sha")
    except Exception:
        log.exception("Failed to get PR details for %s/%s #%s", owner, repo, pr_num)
        return None


def _check_commit_statuses(owner: str, repo: str, sha: str) -> CIResult:
    """Query combined commit status for a SHA and return a CIResult."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}/status"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "linear-pipeline-critic/1.0",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "no statuses")
        statuses = data.get("statuses", [])

        if state == "success":
            return CIResult(ci_found=True, ci_passing=True)

        if state in ("pending", None):
            descriptions = [s.get("description", s["context"]) for s in statuses if s.get("state") == "pending"]
            return CIResult(ci_found=True, ci_passing=False, ci_pending=True, ci_errors=descriptions)

        # failure or error
        errors = []
        for s in statuses:
            if s.get("state") in ("failure", "error"):
                desc = s.get("description") or s.get("context", "unknown check")
                target_url = s.get("target_url") or ""
                if target_url:
                    errors.append(f"{desc} ({target_url})")
                else:
                    errors.append(desc)

        return CIResult(ci_found=True, ci_passing=False, ci_errors=errors)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return CIResult(ci_found=False)
        log.error("GitHub API error checking statuses: %s", e)
        return CIResult(ci_found=False)

    except Exception:
        log.exception("Failed to check commit statuses for %s/%s@%s", owner, repo, sha)
        return CIResult(ci_found=False)


def _check_ci(issue: dict[str, Any]) -> CIResult:
    """Check CI status for the PR linked to this issue."""
    comments = issue.get("comments", {}).get("nodes", [])
    pr_url = _extract_pr_url(comments)
    if not pr_url:
        log.info("No PR URL found in issue comments")
        return CIResult(ci_found=False)

    match = GITHUB_PR_RE.search(pr_url)
    assert match
    owner, repo, pr_num = match.group(1), match.group(2), int(match.group(3))

    sha = _get_latest_commit_sha(owner, repo, pr_num)
    if not sha:
        return CIResult(ci_found=False, ci_errors=["Could not resolve PR head commit"])

    return _check_commit_statuses(owner, repo, sha)


def _verify_endpoints(issue: dict[str, Any]) -> str:
    """Make real HTTP calls to edge function endpoints mentioned in the issue.

    This is the ground-truth check that prevents agents from claiming a fix
    is deployed when it isn't. Returns a formatted string of actual results.
    """
    description = issue.get("description", "") or ""
    title = issue.get("title", "") or ""
    comments = issue.get("comments", {}).get("nodes", [])

    all_text = description + " " + title
    for c in comments:
        all_text += " " + (c.get("body", "") or "")

    urls = ENDPOINT_RE.findall(all_text)
    if not urls:
        return ""

    seen = set()
    lines = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)

        # Try with an Authorization header so 401 vs 404 tells us something
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=False)
            status = resp.status_code
            body_preview = resp.text[:120].replace("\n", " ").strip()
            if status == 200:
                lines.append(f"- ✅ {url} → HTTP 200 (reachable)")
            elif status == 401:
                lines.append(f"- ⚠️ {url} → HTTP 401 (endpoint exists, needs auth)")
            elif status == 404:
                lines.append(f"- ❌ {url} → HTTP 404 (not deployed)")
            else:
                lines.append(f"- ❓ {url} → HTTP {status}: {body_preview}")
        except httpx.ConnectError:
            lines.append(f"- ❌ {url} → Connection failed (DNS/resolve error)")
        except httpx.TimeoutException:
            lines.append(f"- ❌ {url} → Timed out (10s)")
        except Exception as e:
            lines.append(f"- ❌ {url} → {type(e).__name__}: {e}")

    return "\n".join(lines) if lines else ""


def _has_verification_blockers(verification_report: str) -> list[str]:
    """Check endpoint verification results for hard failures."""
    blockers = []
    for line in verification_report.split("\n"):
        line = line.strip()
        if "→ HTTP 404" in line:
            blockers.append(line)
        elif "→ Connection failed" in line:
            blockers.append(line)
        elif "→ Timed out" in line:
            blockers.append(line)
        elif "→ Error" in line or "→ Exception" in line:
            blockers.append(line)
    return blockers


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "critic"}


@app.post("/review")
def review(req: ReviewRequest) -> ReviewDecision:
    issue = linear.get_issue(req.issue_id)
    if issue["team"]["id"] not in settings.team_id_set:
        raise ValueError("team not allowed")

    identifier = issue["identifier"]

    # Step 1: Verify deployed endpoints against ground truth
    # This runs BEFORE the LLM review so the LLM gets the real data
    verification_report = _verify_endpoints(issue)
    verification_blockers = _has_verification_blockers(verification_report)
    if verification_blockers:
        log.info(
            "%s: endpoint verification found %d blocker(s)",
            identifier, len(verification_blockers),
        )

    # Step 2: Check CI status if GITHUB_TOKEN is configured
    ci_check = None
    if settings.github_token:
        ci_check = _check_ci(issue)
        if ci_check.ci_found and not ci_check.ci_passing:
            error_lines = "\n".join(f"- ❌ {e}" for e in ci_check.ci_errors)
            if ci_check.ci_pending:
                comment = f"**CI checks still running — waiting for completion.**\nPending checks:\n{error_lines}"
                decision_label = "changes"
            else:
                comment = f"**Rejected: CI checks are failing.**\n\n{error_lines}\n\nFix the failures and re-trigger CI before requesting review again."
                decision_label = "changes"
            try:
                linear.create_comment(req.issue_id, comment)
                states = _load_state_ids()
                planned_id = states.get("planned")
                if planned_id:
                    linear.update_issue_state(req.issue_id, planned_id)
            except Exception:
                log.exception("Critic write failed %s", req.issue_id)
            return ReviewDecision(decision=decision_label, comment=comment)

    # Step 3: Run LLM review — include verification data if available
    verification_section = ""
    if verification_report:
        verification_section = (
            f"\n\n## Endpoint Verification (ground truth)\n"
            f"The critic service made real HTTP requests to these endpoints:\n"
            f"{verification_report}\n"
            f"\n"
            f"Endpoint verification must be considered in the verdict. "
            f"An endpoint returning 404 means the fix IS NOT deployed."
        )

    prompt = (
        f"{CRITIC_SOUL}\n\n"
        f"Issue: {identifier}\n"
        f"Title: {issue['title']}\n"
        f"Description:\n{issue['description'] or ''}\n"
        f"{verification_section}"
    )

    # Strip API keys so LLM can't use them
    os.environ.pop("LINEAR_API_KEY", None)

    answer = chat([{"role": "user", "content": prompt}])

    # Extract JSON from LLM response
    _match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', answer, re.DOTALL)
    if _match:
        try:
            _data = json.loads(_match.group())
        except json.JSONDecodeError:
            _data = {}
    else:
        _data = {}

    verdict = _data.get("verdict", "changes")
    summary = _data.get("summary", "")
    findings = _data.get("findings", [])
    decision = "approve" if verdict == "approve" else "changes"

    # Override: if endpoint verification found 404s, force "changes"
    if verification_blockers and decision == "approve":
        log.warning(
            "%s: LLM approved but endpoint verification found blockers — overriding to changes",
            identifier,
        )
        decision = "changes"
        summary = "Endpoint verification failed: deployed endpoint(s) are not reachable. The fix is not live."
        findings = [
            f"Endpoint verification failed — these returned errors:\n"
            + "\n".join(f"  - {b}" for b in verification_blockers)
        ]

    # Build clean human-readable comment
    findings_text = "\n".join(f"- {f}" for f in findings) if findings else "- None"
    clean_comment = f"## Review: {'✅ Approved' if decision == 'approve' else '🔄 Changes Requested'}\n\n{summary}\n\n**Findings:**\n{findings_text}"

    # Add verification report section
    full_comment = clean_comment
    if verification_report:
        full_comment += f"\n\n**Endpoint Verification:**\n{verification_report}"
    if settings.github_token and ci_check and not ci_check.ci_found:
        full_comment = f"**Note:** No CI checks found.\n\n---\n\n{clean_comment}"
        if verification_report:
            full_comment += f"\n\n**Endpoint Verification:**\n{verification_report}"
    elif settings.github_token and ci_check and ci_check.ci_passing:
        full_comment = f"✅ CI checks all passing.\n\n---\n\n{clean_comment}"
        if verification_report:
            full_comment += f"\n\n**Endpoint Verification:**\n{verification_report}"

    try:
        linear.create_comment(req.issue_id, full_comment)
        states = _load_state_ids()
        if decision == "approve" and not settings.critic_auto_merge:
            target = "ready-to-merge"
        else:
            target = "done" if decision == "approve" else "planned"
        target_id = states.get(target)
        if target_id:
            linear.update_issue_state(req.issue_id, target_id)
    except Exception:
        log.exception("Critic write failed %s", req.issue_id)

    return ReviewDecision(decision=decision, comment=full_comment)
