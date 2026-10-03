import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routers.repositories import normalize_github_full_name


@pytest.mark.parametrize("raw,expected", [
    ("octocat/Hello-World", "octocat/Hello-World"),
    ("https://github.com/octocat/Hello-World", "octocat/Hello-World"),
    ("https://github.com/octocat/Hello-World.git", "octocat/Hello-World"),
    ("https://github.com/octocat/Hello-World/", "octocat/Hello-World"),
    ("https://github.com/octocat/Hello-World/tree/main", "octocat/Hello-World"),
    ("https://github.com/pallets/flask/blob/main/README.rst", "pallets/flask"),
    ("http://github.com/octocat/Hello-World", "octocat/Hello-World"),
    ("git@github.com:octocat/Hello-World.git", "octocat/Hello-World"),
    ("www.github.com/octocat/Hello-World", "octocat/Hello-World"),
    ("github.com/octocat/Hello-World", "octocat/Hello-World"),
    ("  octocat/Hello-World  ", "octocat/Hello-World"),  # tolerate stray whitespace
])
def test_normalize_accepts_real_world_formats(raw, expected):
    assert normalize_github_full_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "not a valid input at all",
    "https://gitlab.com/octocat/Hello-World",
    "just-one-word",
    "",
])
def test_normalize_rejects_garbage_with_clear_message(raw):
    with pytest.raises(ValueError, match="Could not parse a GitHub repository"):
        normalize_github_full_name(raw)
