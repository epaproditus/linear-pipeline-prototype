from __future__ import annotations

import json
from pathlib import Path

import httpx

from lib.linear_client import LinearClient


def load_env(env_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_path.exists():
        raise SystemExit(f"Missing env file: {env_path}")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    env = load_env(Path(__file__).with_name(".env"))
    api_key = env["LINEAR_API_KEY"]
    webhook_url = env.get("ROUTER_URL")
    if not webhook_url:
        raise SystemExit("Set ROUTER_URL=http://host:8661/triage in .env")
    team_id = env.get("ALLOWED_TEAM_ID")
    client = LinearClient(api_key)

    # Find an issue in needs-triage
    from linear_router import RouterSettings  # noqa: E402
    settings = RouterSettings(
        LINEAR_API_KEY=api_key,
        LINEAR_WEBHOOK_SECRET=env.get("LINEAR_WEBHOOK_SECRET", ""),
        ALLOWED_TEAM_IDS=team_id or "",
        BACKEND_URL=env.get("BACKEND_URL", "http://127.0.0.1:8642/v1"),
        BACKEND_KEY=env.get("BACKEND_KEY", ""),
        MODEL=env.get("MODEL", "hermes-agent"),
        WORKDIR=env.get("WORKDIR", ""),
    )

    # Grab first issue in needs-triage from allowed team
    states = client.get_team_states(team_id)
    needs_triage = next((s["id"] for s in states if s["name"].lower() == "needs-triage"), None)
    if not needs_triage:
        raise SystemExit("No needs-triage state found in team")

    query = """query NeedsTriage($teamId: ID!, $stateId: ID!) {
      issues(filter: { team: { id: { eq: $teamId } }, state: { id: { eq: $stateId } } }, first: 1) {
        nodes { id identifier title state { name } }
      }
    }"""
    data = client._gql(query, {"teamId": team_id, "stateId": needs_triage})
    issues = data["issues"]["nodes"]
    if not issues:
        raise SystemExit("No issues in needs-triage")
    issue_id = issues[0]["id"]
    identifier = issues[0]["identifier"]
    print(f"Smoke triaging {identifier} ({issue_id})")
    resp = httpx.post(webhook_url, json={"issue_id": issue_id}, timeout=10)
    print(resp.status_code, resp.text)


if __name__ == "__main__":
    main()
