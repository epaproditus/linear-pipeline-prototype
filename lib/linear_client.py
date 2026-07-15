"""Shared Linear GraphQL client for pipeline prototype."""

from __future__ import annotations

from typing import Any

import httpx

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

class LinearClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=LINEAR_GRAPHQL_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        )

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = self._client.post("", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        query = """query Issue($id: String!) {
          issue(id: $id) {
            id identifier title description
            state { id name type }
            team { id key name }
            project { id name description }
            labels { nodes { id name } }
            comments { nodes { id body createdAt user { id name } } }
          }
        }"""
        return self._gql(query, {"id": issue_id})["issue"]

    def create_comment(self, issue_id: str, body: str) -> dict[str, Any]:
        mutation = """mutation CommentCreate($input: CommentCreateInput!) {
          commentCreate(input: $input) { success comment { id } }
        }"""
        return self._gql(mutation, {"input": {"issueId": issue_id, "body": body}})

    def get_team_states(self, team_id: str) -> list[dict[str, str]]:
        query = """query TeamStates($teamId: ID!) {
          workflowStates(filter: { team: { id: { eq: $teamId } } }) {
            nodes { id name type }
          }
        }"""
        return list(self._gql(query, {"teamId": team_id})["workflowStates"]["nodes"])

    def update_issue_state(self, issue_id: str, state_id: str) -> dict[str, Any]:
        mutation = """mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) { success issue { id identifier state { id name } } }
        }"""
        return self._gql(mutation, {"id": issue_id, "input": {"stateId": state_id}})
