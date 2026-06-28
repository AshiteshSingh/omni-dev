"""
bug.py - Python conversion of scratch_repo/src/commands/bug.tsx

The /bug command captures a bug report (free-text description plus environment
diagnostics) and stores it locally under the global ``.omni-dev/bugs/`` directory.

Unlike the reference tool — which opened an interactive React form and submitted
feedback to a remote endpoint — this port keeps everything local and offline:
the report is written to disk so the user owns it (Req 16.1).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _resolve_global_dir() -> Path:
    """Resolve the ``.omni-dev`` global directory (mirrors config_store)."""
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".omni-dev"


def _resolve_bugs_dir() -> Path:
    """Resolve the bug-report directory: ``<global>/bugs``."""
    return _resolve_global_dir() / "bugs"


def _active_model() -> str:
    """Best-effort lookup of the currently active model."""
    try:
        from src.config_store import get_global_config

        model = get_global_config().get("activeModel")
        if model:
            return str(model)
    except Exception:
        pass
    return os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro (default)")


def _git_branch() -> str:
    """Best-effort lookup of the current git branch."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            cwd=os.getcwd(),
        )
        if result.returncode == 0:
            return result.stdout.strip() or "(detached HEAD)"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "(not a git repo / git unavailable)"


def _collect_environment() -> Dict[str, Any]:
    """Gather environment diagnostics for the bug report."""
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.platform()})",
        "pythonVersion": sys.version.split()[0],
        "activeModel": _active_model(),
        "gitBranch": _git_branch(),
        "cwd": os.getcwd(),
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    """Render the stored report as readable markdown."""
    env = report["environment"]
    return (
        f"# Bug Report\n\n"
        f"- **Reported:** {report['createdAt']}\n\n"
        f"## Description\n\n{report['description']}\n\n"
        f"## Environment\n\n"
        f"- **OS:** {env['os']}\n"
        f"- **Python:** {env['pythonVersion']}\n"
        f"- **Active model:** {env['activeModel']}\n"
        f"- **Git branch:** {env['gitBranch']}\n"
        f"- **Working directory:** {env['cwd']}\n"
    )


async def bug_command(description: str) -> str:
    """
    Capture a bug report and store it locally (Req 16.1).

    Args:
        description: Free-text description of the problem.
    Returns:
        Confirmation message including the saved path, or a descriptive error.
    """
    description = (description or "").strip()
    if not description:
        return "Please provide a description of the bug, e.g. `/bug the model loops on tool errors`."

    now = datetime.now(timezone.utc)
    report: Dict[str, Any] = {
        "createdAt": now.isoformat(),
        "description": description,
        "environment": _collect_environment(),
    }

    bugs_dir = _resolve_bugs_dir()
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    base_name = f"bug_{stamp}"

    try:
        bugs_dir.mkdir(parents=True, exist_ok=True)
        json_path = bugs_dir / f"{base_name}.json"
        md_path = bugs_dir / f"{base_name}.md"

        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(_render_markdown(report))
    except OSError as e:
        return f"⚠️  Could not save bug report: {e}"

    return (
        "🐛 Bug report saved locally.\n"
        f"  • {md_path}\n"
        f"  • {json_path}"
    )
