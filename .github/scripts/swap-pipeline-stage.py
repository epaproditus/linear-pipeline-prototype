#!/usr/bin/env python3
"""
Swap the pipeline-stage label on a Linear issue.
Usage: python3 swap-pipeline-stage.py <issue-id> <remove-label-id> <add-label-id>

Reads LINEAR_API_KEY from environment.
"""
import json, os, sys, urllib.request, urllib.error

API_KEY = os.environ.get("LINEAR_API_KEY")
if not API_KEY:
    print("ERROR: LINEAR_API_KEY not set", file=sys.stderr)
    sys.exit(1)

issue_id = sys.argv[1]
remove_id = sys.argv[2]
add_id = sys.argv[3]

API_URL = "https://api.linear.app/graphql"
HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
}


def gql(query, variables=None):
    data = {"query": query}
    if variables:
        data["variables"] = variables
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(data).encode(),
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        sys.exit(1)


# 1) Get current label IDs
query = """query($id: ID!) {
  issue(id: $id) { labelIds }
}"""
result = gql(query, {"id": issue_id})
label_ids = result["data"]["issue"]["labelIds"]

# 2) Swap labels: remove old pipeline-stage, add new one
new_ids = [lid for lid in label_ids if lid != remove_id]
if add_id not in new_ids:
    new_ids.append(add_id)

# 3) Update
mutation = """mutation($id: ID!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}"""
result = gql(mutation, {"id": issue_id, "input": {"labelIds": new_ids}})
success = result["data"]["issueUpdate"]["success"]
print(f"pipeline-stage swap {'OK' if success else 'FAILED'}")
sys.exit(0 if success else 1)
