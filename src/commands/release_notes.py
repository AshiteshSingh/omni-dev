"""
release_notes.py - Python conversion of scratch_repo/src/commands/release-notes.ts

The /release-notes command displays the project changelog. It prefers a
CHANGELOG.md found at the project root, then falls back to a bundled
``RELEASE_NOTES`` constant (Req 16.4).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


#: Bundled fallback notes, keyed by version. Mirrors the reference
#: ``constants/releaseNotes.ts`` shape so the dispatcher can show something
#: useful even without a CHANGELOG.md on disk.
RELEASE_NOTES: dict[str, List[str]] = {
    "0.1.0": [
        "Windows-first Python CLI with litellm + Cognee memory.",
        "Slash commands: /doctor, /init, /review, /compact, /config, /ctx-viz.",
        "New utility commands: /bug, /pr-comments, /release-notes, /terminal-setup, /clear, /resume.",
        "Persistent global + per-project config and conversation transcripts.",
        "Cost tracking with configurable cost/token warning thresholds.",
    ],
}


def _find_changelog() -> Optional[Path]:
    """Look for a CHANGELOG.md at the cwd or any ancestor directory."""
    cwd = Path(os.getcwd()).resolve()
    for directory in (cwd, *cwd.parents):
        candidate = directory / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
    return None


def _bundled_notes() -> str:
    """Render the bundled RELEASE_NOTES constant as text."""
    lines: List[str] = ["## Release Notes\n"]
    for version, notes in RELEASE_NOTES.items():
        lines.append(f"### v{version}")
        lines.extend(f"• {note}" for note in notes)
        lines.append("")
    return "\n".join(lines).rstrip()


async def release_notes_command() -> str:
    """
    Display release notes / changelog (Req 16.4).

    Returns:
        The contents of CHANGELOG.md if present, otherwise the bundled notes.
    """
    changelog = _find_changelog()
    if changelog is not None:
        try:
            text = changelog.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return f"## Release Notes (from {changelog})\n\n{text}"
        except OSError:
            # Fall through to bundled notes on read failure.
            pass

    return _bundled_notes()
