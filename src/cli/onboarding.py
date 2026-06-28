"""
onboarding.py - First-run project trust prompt for the Omni-Dev CLI.

When Omni-Dev starts in a project directory for which the user has not yet
accepted the first-run trust prompt, it must explain that the agent can read,
run, and modify files in that directory and ask the user to accept before any
tasks are processed (Req 16.7). Once accepted, the acceptance is persisted to
the Project_Config (``hasTrustDialogAccepted``) so the prompt is never shown
again for that project (Req 16.8).

Mirrors the intent of the reference ``TrustDialog`` / ``Onboarding`` components
(``scratch_repo/src/components/TrustDialog.tsx``), adapted to a Rich-based
terminal UI and styled through :mod:`src.cli.theme`.

Design notes:
- ``hasTrustDialogAccepted`` is read/written through :mod:`src.config_store`'s
  per-project config (keyed by absolute path).
- The interactive prompt runs blocking input inside an executor so it stays
  friendly to the surrounding asyncio event loop.
- The flow is defensive: in a non-interactive environment (no TTY) it does not
  hang waiting for input; it defaults to trusted so automated/piped runs work.

This module is import-safe: importing it performs no I/O and has no side
effects. Wiring the trust gate into the interface/entry point is handled
separately (task 18).
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src import config_store

#: The Project_Config key recording first-run trust acceptance (Req 16.7, 16.8).
TRUST_KEY = "hasTrustDialogAccepted"


# ─────────────────────────────────────────────────────────────────────────────
# Trust state (read / persist)
# ─────────────────────────────────────────────────────────────────────────────
def is_project_trusted(path: Optional[str] = None) -> bool:
    """Return whether the project at ``path`` (defaults to cwd) is trusted.

    Reads ``hasTrustDialogAccepted`` from the Project_Config. Defensive against
    a missing/falsey value (treated as untrusted) so a fresh project always
    prompts.
    """
    try:
        cfg = config_store.get_project_config(path)
    except Exception:
        # Config layer already falls back to defaults; any unexpected error
        # here should be treated as "not yet trusted" rather than crashing.
        return False
    return bool(cfg.get(TRUST_KEY, False))


def mark_project_trusted(path: Optional[str] = None) -> None:
    """Persist Project_Trust for the project at ``path`` (defaults to cwd).

    Sets ``hasTrustDialogAccepted`` to ``True`` in the Project_Config and saves
    it so the trust prompt is not shown again for this project (Req 16.8).
    """
    cfg = config_store.get_project_config(path)
    cfg[TRUST_KEY] = True
    config_store.save_project_config(cfg, path)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────
def _is_interactive() -> bool:
    """Return ``True`` when stdin/stdout are attached to an interactive TTY.

    Used to avoid hanging on input in non-interactive contexts (pipes, CI,
    captured stdio). Any error during detection is treated as non-interactive.
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _render_trust_prompt(console: Console, project_path: str) -> None:
    """Render the themed first-run trust prompt body (Req 16.7)."""
    body = Text()
    body.append("Omni-Dev is about to start in this directory:\n\n", style="default")
    body.append(f"  {project_path}\n\n", style="app.accent")
    body.append(
        "While working here, the agent can read files, run commands, and "
        "modify files in this directory and its subfolders.\n\n",
        style="default",
    )
    body.append(
        "Only continue if you trust the contents of this folder. "
        "Running an agent against untrusted files may have unintended effects.",
        style="app.muted",
    )

    panel = Panel(
        body,
        title=Text("Do you trust the files in this folder?", style="status.warn"),
        border_style="status.warn",
        padding=(1, 2),
    )
    console.print(panel)


def _blocking_confirm() -> bool:
    """Prompt the user to accept/decline trust, blocking on stdin.

    Returns ``True`` on accept, ``False`` on decline. Defensive against EOF or
    interrupt (treated as a decline so the caller can exit/restrict safely).
    """
    from rich.prompt import Confirm

    try:
        return bool(
            Confirm.ask(
                Text("Proceed and trust this folder?", style="app.accent"),
                default=False,
            )
        )
    except (EOFError, KeyboardInterrupt):
        return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Onboarding flow
# ─────────────────────────────────────────────────────────────────────────────
async def run_onboarding_if_needed(console: Console, path: Optional[str] = None) -> bool:
    """Run the first-run trust prompt for ``path`` if it is not yet trusted.

    Behavior (Req 16.7, 16.8):
    - If the project is already trusted, return ``True`` immediately without
      prompting.
    - In a non-interactive environment (no TTY), do not block on input; default
      to trusted (``True``) so piped/automated runs proceed.
    - Otherwise display the themed trust prompt and ask the user to accept or
      decline before processing tasks. On accept, persist Project_Trust via
      :func:`mark_project_trusted` and return ``True``. On decline, return
      ``False`` so the caller can exit or restrict.

    Returns ``True`` when the project is trusted (already, defaulted, or just
    accepted) and ``False`` when the user declined.
    """
    # Already trusted: nothing to do.
    if is_project_trusted(path):
        return True

    # Resolve the path we will display / persist against. Falling back to cwd
    # keeps the displayed path and the persisted key consistent.
    import os

    project_path = os.path.abspath(path) if path is not None else os.getcwd()

    # Non-interactive: do not hang waiting for input. Default to trusted so
    # automated/piped invocations continue working.
    if not _is_interactive():
        return True

    _render_trust_prompt(console, project_path)

    # Run the blocking prompt in an executor so we stay async-friendly.
    loop = asyncio.get_event_loop()
    accepted = await loop.run_in_executor(None, _blocking_confirm)

    if accepted:
        try:
            mark_project_trusted(path)
        except Exception:
            # Persistence failure should not block the session; the user still
            # accepted, they will simply be prompted again next time.
            console.print(
                Text("Could not persist trust setting; you may be asked again next time.", style="status.warn")
            )
        console.print(Text("Folder trusted. Continuing\u2026", style="status.ok"))
        return True

    console.print(Text("Trust declined.", style="status.err"))
    return False
