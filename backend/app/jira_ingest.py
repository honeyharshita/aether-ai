"""
Jira ingestion adapter (spec Section 4 / 53).

HONEST STATUS: unlike app/github_ingest.py (which was tested against the
live GitHub API in this build), this module has NOT been exercised
against a real Jira instance -- Atlassian's API domains are not reachable
from the sandbox this project was built in. It is written to the same
structure and error-handling pattern as github_ingest.py (real HTTP
calls, no fabricated data, GitHubUnavailableError-equivalent handling)
and follows Jira Cloud REST API v3's documented shapes, but treat it like
the Terraform/Kubernetes files: syntactically and structurally sound,
review and test against a real Jira instance before relying on it.

To use: set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env, then wire
a router calling these functions the same way repositories.py calls
github_ingest.py.
"""
import base64
import requests
from app.config import settings

STATUS_MAP = {
    # Normalizes common Jira workflow statuses onto AetherAI's internal
    # BACKLOG/TODO/IN_PROGRESS/IN_REVIEW/TESTING/DONE (spec Section 52).
    "To Do": "TODO", "Backlog": "BACKLOG", "In Progress": "IN_PROGRESS",
    "In Review": "IN_REVIEW", "Testing": "TESTING", "QA": "TESTING",
    "Done": "DONE", "Closed": "DONE", "Resolved": "DONE",
}

PRIORITY_MAP = {
    "Highest": "critical", "High": "high", "Medium": "medium",
    "Low": "low", "Lowest": "low",
}


class JiraUnavailableError(Exception):
    pass


def _auth_header(email: str, api_token: str) -> dict:
    token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def fetch_project_issues(jira_base_url: str, project_key: str, email: str, api_token: str,
                          max_results: int = 50) -> list:
    """
    GET /rest/api/3/search?jql=project={key}
    Returns normalized issue dicts ready to map onto the Issue model.
    """
    url = f"{jira_base_url.rstrip('/')}/rest/api/3/search"
    params = {"jql": f"project={project_key}", "maxResults": max_results}
    try:
        resp = requests.get(url, headers=_auth_header(email, api_token), params=params, timeout=10)
    except requests.RequestException as e:
        raise JiraUnavailableError(f"Could not reach Jira: {e}")

    if resp.status_code != 200:
        raise JiraUnavailableError(f"Jira returned {resp.status_code}: {resp.text[:200]}")

    issues = []
    for item in resp.json().get("issues", []):
        fields = item.get("fields", {})
        issues.append({
            "external_id": item.get("key"),
            "title": fields.get("summary", ""),
            "status": STATUS_MAP.get((fields.get("status") or {}).get("name"), "TODO"),
            "priority": PRIORITY_MAP.get((fields.get("priority") or {}).get("name"), "medium"),
            "issue_type": (fields.get("issuetype") or {}).get("name", "Task"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "sprint": (fields.get("customfield_10020") or [{}])[0].get("name")
                      if fields.get("customfield_10020") else None,
            "story_points": fields.get("customfield_10016"),  # field id varies per Jira instance
        })
    return issues


def fetch_sprint_report(jira_base_url: str, board_id: int, sprint_id: int, email: str, api_token: str) -> dict:
    """GET /rest/agile/1.0/sprint/{sprint_id}/issue -- velocity/burndown source data."""
    url = f"{jira_base_url.rstrip('/')}/rest/agile/1.0/sprint/{sprint_id}/issue"
    try:
        resp = requests.get(url, headers=_auth_header(email, api_token), timeout=10)
    except requests.RequestException as e:
        raise JiraUnavailableError(f"Could not reach Jira: {e}")
    if resp.status_code != 200:
        raise JiraUnavailableError(f"Jira returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()
