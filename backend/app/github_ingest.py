"""
Real GitHub ingestion (spec Section 4).

Uses the public GitHub REST API (api.github.com) to pull real repository
metadata, real commit history, and real file contents for a connected
public repository. No values here are invented -- if GitHub is
unreachable or rate-limited, the caller gets a clear error rather than a
silently-faked response (spec Section 57: "If GitHub is unavailable,
display a graceful message; do not crash").
"""
import requests
from app.config import settings

GITHUB_API = "https://api.github.com"


def _headers(token: str = None):
    headers = {"Accept": "application/vnd.github+json"}
    access_token = token or settings.GITHUB_TOKEN
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


class GitHubUnavailableError(Exception):
    pass


def fetch_repo_metadata(full_name: str, token: str = None) -> dict:
    resp = requests.get(f"{GITHUB_API}/repos/{full_name}", headers=_headers(token), timeout=10)
    if resp.status_code != 200:
        raise GitHubUnavailableError(f"GitHub returned {resp.status_code} for {full_name}: {resp.text[:200]}")
    return resp.json()


def fetch_recent_commits(full_name: str, per_page: int = 30, token: str = None) -> list:
    resp = requests.get(
        f"{GITHUB_API}/repos/{full_name}/commits",
        headers=_headers(token), params={"per_page": per_page}, timeout=10,
    )
    if resp.status_code != 200:
        raise GitHubUnavailableError(f"GitHub returned {resp.status_code} fetching commits for {full_name}")
    commits = []
    for c in resp.json():
        stats_resp = requests.get(c["url"], headers=_headers(token), timeout=10)
        stats = stats_resp.json() if stats_resp.status_code == 200 else {}
        commits.append({
            "sha": c["sha"],
            "author": (c.get("commit", {}).get("author") or {}).get("name", "unknown"),
            "message": c.get("commit", {}).get("message", ""),
            "committed_at": (c.get("commit", {}).get("author") or {}).get("date"),
            "additions": stats.get("stats", {}).get("additions", 0),
            "deletions": stats.get("stats", {}).get("deletions", 0),
            "changed_files": len(stats.get("files", [])),
            "files": [f["filename"] for f in stats.get("files", [])],
        })
    return commits


def fetch_repo_tree_files(full_name: str, branch: str = None, max_files: int = 60, token: str = None) -> list:
    """Return a list of source file paths + raw content for metric analysis."""
    meta = fetch_repo_metadata(full_name, token=token)
    branch = branch or meta.get("default_branch", "main")
    resp = requests.get(
        f"{GITHUB_API}/repos/{full_name}/git/trees/{branch}",
        headers=_headers(token), params={"recursive": "1"}, timeout=10,
    )
    if resp.status_code != 200:
        raise GitHubUnavailableError(f"GitHub returned {resp.status_code} fetching tree for {full_name}")
    tree = resp.json().get("tree", [])
    code_exts = (".py", ".js", ".ts", ".java", ".go", ".rb", ".c", ".cpp", ".jsx", ".tsx")
    files = [t for t in tree if t["type"] == "blob" and t["path"].endswith(code_exts)][:max_files]

    results = []
    for f in files:
        blob_resp = requests.get(f["url"], headers=_headers(token), timeout=10)
        if blob_resp.status_code != 200:
            continue
        blob = blob_resp.json()
        import base64
        try:
            content = base64.b64decode(blob.get("content", "")).decode("utf-8", errors="ignore")
        except Exception:
            content = ""
        results.append({"path": f["path"], "content": content, "size": f.get("size", 0)})
    return results
