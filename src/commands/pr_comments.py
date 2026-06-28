"""
pr_comments.py - Python conversion of scratch_repo/src/commands/pr_comments.ts

The /pr-comments command fetches GitHub pull-request review comments using the
``gh`` CLI (preferred) and summarizes them. The reference implementation handed
the agent a prompt that drove ``gh`` itself; this port runs ``gh`` directly so the
command works without an LLM round-trip.

Per Req 16.3, when ``gh``/``git`` is unavailable, offline, or errors, this returns
a descriptive error string WITHOUT raising, so the session keeps running.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import List, Optional


_TIMEOUT = 20


def _run(args: List[str]) -> subprocess.CompletedProcess:
    """Run a subprocess with safe decoding and a timeout."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT,
        cwd=os.getcwd(),
    )


def _tool_available(cmd: str) -> bool:
    """Return True if ``cmd --version`` runs successfully."""
    try:
        result = _run([cmd, "--version"])
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _summarize_comments(issue_comments: list, review_comments: list) -> str:
    """Format PR-level and review comments into a readable summary."""
    lines: List[str] = ["## PR Comments\n"]

    if issue_comments:
        lines.append(f"### 💬 Discussion ({len(issue_comments)})")
        for c in issue_comments:
            author = (c.get("user") or {}).get("login", "unknown")
            body = (c.get("body") or "").strip().replace("\n", "\n  ")
            lines.append(f"- @{author}:\n  {body}")
        lines.append("")

    if review_comments:
        lines.append(f"### 🔍 Code review ({len(review_comments)})")
        for c in review_comments:
            author = (c.get("user") or {}).get("login", "unknown")
            path = c.get("path", "?")
            line = c.get("line") or c.get("original_line") or "?"
            body = (c.get("body") or "").strip().replace("\n", "\n  ")
            lines.append(f"- @{author} {path}#{line}:\n  {body}")
        lines.append("")

    if not issue_comments and not review_comments:
        return "No comments found on this pull request."

    return "\n".join(lines).rstrip()


async def pr_comments_command(target: str = "") -> str:
    """
    Fetch and summarize GitHub PR review comments (Req 16.2, 16.3).

    Args:
        target: Optional PR number, URL, or branch. Defaults to the PR associated
            with the current branch.
    Returns:
        A formatted summary, or a descriptive error string (never raises).
    """
    target = (target or "").strip()

    if not _tool_available("gh"):
        if _tool_available("git"):
            return (
                "⚠️  The GitHub CLI (`gh`) is required to fetch PR comments and was not found.\n"
                "    `git` is available but cannot retrieve pull-request review comments on its own.\n"
                "    Install `gh` from https://cli.github.com/ and run `gh auth login`."
            )
        return (
            "⚠️  Neither `gh` nor `git` is available. Install the GitHub CLI "
            "(https://cli.github.com/) and authenticate with `gh auth login` to use this command."
        )

    # Resolve PR number + repository (owner/repo) for the API calls.
    try:
        view_args = ["gh", "pr", "view"]
        if target:
            view_args.append(target)
        view_args += ["--json", "number,headRepository,headRepositoryOwner"]
        view = _run(view_args)
    except subprocess.TimeoutExpired:
        return "⚠️  Timed out talking to GitHub. Check your network connection and try again."
    except (FileNotFoundError, OSError) as e:
        return f"⚠️  Could not run `gh`: {e}"

    if view.returncode != 0:
        detail = (view.stderr or view.stdout or "").strip()
        return (
            "⚠️  Could not resolve a pull request"
            + (f" for '{target}'" if target else " for the current branch")
            + ".\n    "
            + (detail or "Make sure you are on a branch with an open PR, or pass a PR number/URL.")
        )

    try:
        meta = json.loads(view.stdout or "{}")
    except ValueError:
        return "⚠️  Received an unexpected response from `gh` while resolving the pull request."

    number = meta.get("number")
    repo = (meta.get("headRepository") or {}).get("name")
    owner = (meta.get("headRepositoryOwner") or {}).get("login")
    if not (number and repo and owner):
        return "⚠️  Could not determine the repository/PR number from `gh`. Try passing an explicit PR number or URL."

    base = f"/repos/{owner}/{repo}"
    try:
        issue_resp = _run(["gh", "api", f"{base}/issues/{number}/comments"])
        review_resp = _run(["gh", "api", f"{base}/pulls/{number}/comments"])
    except subprocess.TimeoutExpired:
        return "⚠️  Timed out fetching comments from GitHub. Check your network connection and try again."
    except (FileNotFoundError, OSError) as e:
        return f"⚠️  Could not run `gh api`: {e}"

    if issue_resp.returncode != 0 and review_resp.returncode != 0:
        detail = (issue_resp.stderr or review_resp.stderr or "").strip()
        return f"⚠️  GitHub API request failed: {detail or 'unknown error'}"

    def _parse(resp: subprocess.CompletedProcess) -> list:
        if resp.returncode != 0:
            return []
        try:
            data = json.loads(resp.stdout or "[]")
            return data if isinstance(data, list) else []
        except ValueError:
            return []

    return _summarize_comments(_parse(issue_resp), _parse(review_resp))
