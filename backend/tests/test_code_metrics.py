import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.code_metrics import compute_loc, compute_cyclomatic_complexity, aggregate_module_metrics


def test_compute_loc_ignores_blank_lines():
    content = "line1\n\nline2\n   \nline3"
    assert compute_loc(content) == 3


def test_python_complexity_uses_radon_ast():
    code = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    result = compute_cyclomatic_complexity("a.py", code)
    assert result["method"] == "radon_ast"
    assert result["value"] >= 1


def test_non_python_complexity_uses_heuristic():
    code = "function f(x){ if(x){ return 1; } return 0; }"
    result = compute_cyclomatic_complexity("a.js", code)
    assert result["method"] == "branch_token_heuristic"
    assert result["value"] >= 1


def test_aggregate_module_metrics_counts_real_commit_data():
    files = [{"path": "a.py", "content": "def f():\n    return 1\n", "size": 20}]
    commits = [
        {"sha": "1", "author": "alice", "additions": 10, "deletions": 0, "files": ["a.py"]},
        {"sha": "2", "author": "bob", "additions": 5, "deletions": 1, "files": ["a.py"]},
        {"sha": "3", "author": "alice", "additions": 2, "deletions": 0, "files": ["other.py"]},
    ]
    result = aggregate_module_metrics(files, commits)
    assert len(result) == 1
    assert result[0]["num_commits"] == 2  # only commits touching a.py
    assert result[0]["num_authors"] == 2  # alice + bob
    assert result[0]["churn_30d"] == 16  # 10+0 + 5+1
