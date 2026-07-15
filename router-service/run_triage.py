from __future__ import annotations

from pathlib import Path

from lib.linear_client import LinearClient
from linear_router import RouterSettings, app
from lib.backend import chat as backend_chat
import httpx


def main() -> None:
    settings = RouterSettings()
    if not settings.linear_api_key:
        raise SystemExit("Set LINEAR_API_KEY in router-service/.env")
    webhook_url = "http://127.0.0.1:8670/triage"
    client = LinearClient(settings.linear_api_key)
    team_id = settings.allowed_team_ids.split(",")[0].strip()
    states = client.get_team_states(team_id)
    print("DEBUG team_id=", team_id)
    print("DEBUG states=", states)
    needs_triage = next((s["id"] for s in states if s["name"].lower() == "todo"), None)
    if not needs_triage:
        raise SystemExit("No Todo state found in team")
    query = """query NeedsTriage($teamId: ID!, $stateId: ID!) {
      issues(filter: { team: { id: { eq: $teamId } }, state: { id: { eq: $stateId } } }, first: 1) {
        nodes { id identifier title state { name } }
      }
    }"""
    data = client._gql(query, {"teamId": team_id, "stateId": needs_triage})
    issues = data["issues"]["nodes"]
    if not issues:
        raise SystemExit("No issues in Todo")
    issue_id = issues[0]["id"]
    identifier = issues[0]["identifier"]
    print(f"Smoke triaging {identifier} ({issue_id})")
    resp = httpx.post(webhook_url, json={"issue_id": issue_id}, timeout=120)
    print(resp.status_code, resp.text)


if __name__ == "__main__":
    main()
