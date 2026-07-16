#!/usr/bin/env python3
"""
Setup Linear custom fields for the factory pipeline (PLY-288).

Usage:
    export LINEAR_API_KEY=lin_api_xxx
    python scripts/setup-custom-fields.py [--team PLY]

Creates two select-type custom fields on the target team:
  - triage-state (Select): unprocessed, ready-to-implement, ready-to-spec,
    needs-info, wait-to-implement, in-implementation, in-review,
    in-verification, done
  - pipeline-stage (Select): triage, spec, implement, review, verify, complete

Also idempotently creates pipeline workflow states (needs-triage, Ready,
Blocked, Planned, In Review, Done) if they don't already exist.

╔══════════════════════════════════════════════════════════════════════╗
║  NOTE: Linear's public GraphQL API does NOT expose a mutation to   ║
║  create custom fields. This script works around that limitation by ║
║  creating label groups, which render as select-type dropdowns in   ║
║  the Linear UI and serve the same purpose for pipeline metadata.   ║
║                                                                    ║
║  If/when Linear adds a public custom field API, update this script ║
║  to use the official mutations.                                    ║
╚══════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

# ── Colours ──────────────────────────────────────────────────────────────────

DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ── Helpers ──────────────────────────────────────────────────────────────────


def gql(
    query: str, variables: dict[str, Any] | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """POST a GraphQL query/mutation to the Linear API."""
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        print(f"{RED}ERROR: LINEAR_API_KEY environment variable is required{RESET}")
        sys.exit(1)

    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    if dry_run:
        print(f"  {DIM}[dry-run] Would POST: {query[:80]}...{RESET}")
        return {"_dry_run": True}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        LINEAR_GRAPHQL_URL,
        data=data,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"{RED}HTTP {e.code}: {e.reason}{RESET}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"{RED}Network error: {e.reason}{RESET}")
        sys.exit(1)

    if "errors" in body:
        print(f"{RED}GraphQL error: {body['errors']}{RESET}")
        sys.exit(1)

    return body["data"]


# ── Queries ──────────────────────────────────────────────────────────────────


def find_team(key: str) -> dict[str, str]:
    """Look up a team by its key (e.g. 'PLY')."""
    q = """query($key: String!) {
      teams(filter: { key: { eq: $key } }) { nodes { id name key } }
    }"""
    data = gql(q, {"key": key})
    nodes = data["teams"]["nodes"]
    if not nodes:
        print(f"{RED}No team found with key '{key}'.{RESET}")
        sys.exit(1)
    return nodes[0]


def list_workflow_states(team_id: str) -> list[dict[str, str]]:
    q = """query TeamStates($teamId: ID!) {
      workflowStates(filter: { team: { id: { eq: $teamId } } }) {
        nodes { id name type }
      }
    }"""
    return gql(q, {"teamId": team_id})["workflowStates"]["nodes"]


def list_labels(team_id: str) -> list[dict[str, Any]]:
    """Return all labels belonging to *any* team in the workspace."""
    q = """query TeamLabels($teamId: ID!) {
      issueLabels(filter: { team: { id: { eq: $teamId } } }) {
        nodes { id name parent { id name } isGroup archivedAt }
      }
    }"""
    return gql(q, {"teamId": team_id})["issueLabels"]["nodes"]


# ── Mutations ────────────────────────────────────────────────────────────────


def create_workflow_state(
    team_id: str, name: str, state_type: str, color: str, position: float
) -> dict[str, Any]:
    """Create a pipeline workflow state.
    Valid types: backlog, unstarted, started, completed, canceled.
    """
    q = """mutation CreateState($input: WorkflowStateCreateInput!) {
      workflowStateCreate(input: $input) {
        success workflowState { id name type }
      }
    }"""
    return gql(
        q,
        {
            "input": {
                "teamId": team_id,
                "name": name,
                "type": state_type,
                "color": color,
                "position": position,
            }
        },
    )


def create_label_group(team_id: str, name: str, color: str) -> dict[str, Any]:
    """Create a label group (the header of a select-type custom field)."""
    q = """mutation CreateLabelGroup($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) {
        success issueLabel { id name }
      }
    }"""
    return gql(
        q,
        {
            "input": {
                "teamId": team_id,
                "name": name,
                "isGroup": True,
                "color": color,
            }
        },
    )


def create_label_option(
    team_id: str, name: str, parent_id: str, color: str
) -> dict[str, Any]:
    """Create an option inside a label group."""
    q = """mutation CreateLabelOption($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) {
        success issueLabel { id name }
      }
    }"""
    return gql(
        q,
        {
            "input": {
                "teamId": team_id,
                "name": name,
                "parentId": parent_id,
                "color": color,
            }
        },
    )


# ── Definitions ──────────────────────────────────────────────────────────────

PIPELINE_STATES = [
    ("needs-triage", "unstarted", "#888888", 0.0),
    ("Ready", "unstarted", "#00aa88", 1.0),
    ("Blocked", "unstarted", "#dd0000", 2.0),
    ("Planned", "started", "#0088aa", 3.0),
    ("In Review", "started", "#aa8800", 4.0),
    ("Done", "completed", "#00aa00", 5.0),
]

TRIAGE_STATE_OPTIONS = [
    "unprocessed",
    "ready-to-implement",
    "ready-to-spec",
    "needs-info",
    "wait-to-implement",
    "in-implementation",
    "in-review",
    "in-verification",
    "done",
]

PIPELINE_STAGE_OPTIONS = [
    "triage",
    "spec",
    "implement",
    "review",
    "verify",
    "complete",
]


# ── Colour palette for options ──────────────────────────────────────────────

# Pastel-ish palette that cycles
OPTION_COLORS = [
    "#6b5b95",
    "#4cb782",
    "#f7b731",
    "#e8634a",
    "#3d99f5",
    "#a569bd",
    "#36aba4",
    "#d98e48",
    "#65c3ba",
]


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Setup Linear custom fields for the factory pipeline"
    )
    parser.add_argument(
        "--team",
        default="PLY",
        help="Team key (default: PLY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making changes",
    )
    args = parser.parse_args()

    team_key = args.team
    dry_run = args.dry_run

    print(f"\n{BOLD}🔧 Factory Pipeline Setup — Custom Fields{RESET}")
    print(f"   Team: {team_key}\n")

    # ── 1. Resolve team ─────────────────────────────────────────────────────
    team = find_team(team_key)
    team_id = team["id"]
    print(f"✅ Resolved team {team['name']} (key={team['key']}, id={team_id})\n")

    # ── 2. Workflow states ──────────────────────────────────────────────────
    print(f"{BOLD}── Workflow States ──{RESET}")
    existing_states = list_workflow_states(team_id)
    existing_state_names = {s["name"].lower() for s in existing_states}

    for name, state_type, color, pos in PIPELINE_STATES:
        if name.lower() in existing_state_names:
            print(f"  ⏩  State '{name}' already exists")
        else:
            if dry_run:
                print(f"  📋  Would create state '{name}' ({state_type})")
                continue
            result = create_workflow_state(team_id, name, state_type, color, pos)
            success = result.get("workflowStateCreate", {}).get("success", False)
            if success:
                print(f"  {GREEN}✅  Created state '{name}' ({state_type}){RESET}")
            else:
                print(f"  {RED}❌  Failed to create state '{name}': {result}{RESET}")

    # ── 3. Label groups (custom fields) ─────────────────────────────────────
    print(f"\n{BOLD}── Custom Fields (as Label Groups) ──{RESET}")

    existing_labels = list_labels(team_id)
    # Filter out archived labels
    active_labels = [l for l in existing_labels if l.get("archivedAt") is None]

    # Map label name → label record
    label_by_name: dict[str, dict[str, Any]] = {}
    for l in active_labels:
        label_by_name[l["name"].lower()] = l

    # ── 3a. triage-state ───────────────────────────────────────────────────
    TRIAGE_GROUP_NAME = "triage-state"
    triage_parent: dict[str, Any] | None = label_by_name.get(TRIAGE_GROUP_NAME)

    if triage_parent:
        print(f"  ⏩  Group '{TRIAGE_GROUP_NAME}' already exists (id={triage_parent['id']})")
    else:
        if dry_run:
            print(f"  📋  Would create group '{TRIAGE_GROUP_NAME}'")
            triage_parent = {"id": "__DRY_RUN__"}
        else:
            result = create_label_group(team_id, TRIAGE_GROUP_NAME, "#6b5b95")
            success = result.get("issueLabelCreate", {}).get("success", False)
            if success:
                triage_parent = result["issueLabelCreate"]["issueLabel"]
                print(f"  {GREEN}✅  Created group '{TRIAGE_GROUP_NAME}' (id={triage_parent['id']}){RESET}")
            else:
                print(f"  {RED}❌  Failed to create group '{TRIAGE_GROUP_NAME}': {result}{RESET}")
                triage_parent = None

    if triage_parent and triage_parent.get("id") != "__DRY_RUN__":
        # Collect existing child labels for this group
        existing_children = {
            l["name"].lower()
            for l in active_labels
            if l.get("parent") and l["parent"]["id"] == triage_parent["id"]
        }
        for i, option in enumerate(TRIAGE_STATE_OPTIONS):
            if option.lower() in existing_children:
                print(f"    ⏩  Option '{option}' already exists")
            else:
                if dry_run:
                    print(f"    📋  Would create option '{option}'")
                else:
                    color = OPTION_COLORS[i % len(OPTION_COLORS)]
                    result = create_label_option(
                        team_id, option, triage_parent["id"], color
                    )
                    success = result.get("issueLabelCreate", {}).get("success", False)
                    if success:
                        print(f"    {GREEN}✅  Created option '{option}'{RESET}")
                    else:
                        print(f"    {RED}❌  Failed to create option '{option}': {result}{RESET}")

    # ── 3b. pipeline-stage ─────────────────────────────────────────────────
    STAGE_GROUP_NAME = "pipeline-stage"
    stage_parent: dict[str, Any] | None = label_by_name.get(STAGE_GROUP_NAME)

    if stage_parent:
        print(f"  ⏩  Group '{STAGE_GROUP_NAME}' already exists (id={stage_parent['id']})")
    else:
        if dry_run:
            print(f"  📋  Would create group '{STAGE_GROUP_NAME}'")
            stage_parent = {"id": "__DRY_RUN__"}
        else:
            result = create_label_group(team_id, STAGE_GROUP_NAME, "#3d99f5")
            success = result.get("issueLabelCreate", {}).get("success", False)
            if success:
                stage_parent = result["issueLabelCreate"]["issueLabel"]
                print(f"  {GREEN}✅  Created group '{STAGE_GROUP_NAME}' (id={stage_parent['id']}){RESET}")
            else:
                print(f"  {RED}❌  Failed to create group '{STAGE_GROUP_NAME}': {result}{RESET}")
                stage_parent = None

    if stage_parent and stage_parent.get("id") != "__DRY_RUN__":
        existing_children = {
            l["name"].lower()
            for l in active_labels
            if l.get("parent") and l["parent"]["id"] == stage_parent["id"]
        }
        for i, option in enumerate(PIPELINE_STAGE_OPTIONS):
            if option.lower() in existing_children:
                print(f"    ⏩  Option '{option}' already exists")
            else:
                if dry_run:
                    print(f"    📋  Would create option '{option}'")
                else:
                    color = OPTION_COLORS[(i + 3) % len(OPTION_COLORS)]
                    result = create_label_option(
                        team_id, option, stage_parent["id"], color
                    )
                    success = result.get("issueLabelCreate", {}).get("success", False)
                    if success:
                        print(f"    {GREEN}✅  Created option '{option}'{RESET}")
                    else:
                        print(f"    {RED}❌  Failed to create option '{option}': {result}{RESET}")

    # ── 4. Summary ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'=' * 60}{RESET}")

    if dry_run:
        print(f"{YELLOW}🔶 Dry-run complete. Pass --dry-run to preview, omit to execute.{RESET}")
    else:
        print(f"{GREEN}✅ Pipeline custom-fields setup complete for {team['name']}.{RESET}")

    print(f"\n{BOLD}How triage-state and pipeline-stage work:{RESET}")
    print(f"  1. These are created as label groups in Linear.")
    print(f"  2. In the Linear UI, they appear as select-type dropdown fields")
    print(f"     on the issue sidebar (via label groups).")
    print(f"  3. Pipeline stages read/write them via the Labels API:")
    print(f"     - issueUpdate {{ labelIds: [\"<option_label_id>\"] }}")
    print("     - issue { labels { nodes { name parent { name } } } }")
    print()
    print(f"{BOLD}Limitation:{RESET}")
    print(f"  Linear's public GraphQL API does not yet expose mutations")
    print(f"  for creating true custom fields (Issue Properties). Label")
    print(f"  groups replicate the UI behavior. Update this script when")
    print(f"  Linear adds the official mutation.")
    print()
    print(f"{BOLD}Next steps:{RESET}")
    print(f"  1. Set TRIAGE_STATE_GROUP_ID = '{triage_parent['id'] if triage_parent else ''}'")
    print(f"     and PIPELINE_STAGE_GROUP_ID = '{stage_parent['id'] if stage_parent else ''}'")
    print(f"     in each pipeline service's configuration.")
    print(f"  2. Pipeline stages should query/set issue labels under these")
    print(f"     groups to track triage-state and pipeline-stage metadata.")


if __name__ == "__main__":
    main()
