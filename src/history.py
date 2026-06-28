"""Bounded, persistent command history backed by the Project_Config.

Mirrors the reference TypeScript model (``scratch_repo/src/history.ts``): the
project's input history is stored in ``Project_Config.history`` as a list ordered
most-recent-first, bounded to ``MAX_HISTORY_ITEMS`` entries, with no consecutive
duplicate of the most recent entry.

Behavior contract (Requirement 12.6-12.8):
- ``get_history`` returns the persisted history most-recent-first, or ``[]`` when
  absent (12.8).
- ``add_to_history`` prepends a new command to the front and truncates to
  ``MAX_HISTORY_ITEMS`` (12.6); it does nothing when the command equals the
  current most-recent entry (no consecutive duplicate) (12.7); empty or
  whitespace-only commands are ignored.
"""

from __future__ import annotations

from typing import List

from . import config_store

#: Maximum number of retained Command_History entries (reference value: 100).
MAX_HISTORY_ITEMS = 100


def get_history() -> List[str]:
    """Return the persisted Command_History ordered most-recent-first (12.8).

    Returns an empty list when no history has been persisted.
    """
    project_config = config_store.get_project_config()
    history = project_config.get("history")
    if not isinstance(history, list):
        return []
    return list(history)


def add_to_history(command: str) -> None:
    """Prepend ``command`` to the persisted Command_History.

    - Skips empty/whitespace-only commands.
    - Does nothing if ``command`` equals the current most-recent entry, so no
      consecutive duplicate is recorded (12.7).
    - Otherwise prepends to the front (most-recent-first) and truncates to
      ``MAX_HISTORY_ITEMS``, discarding the oldest entries beyond the limit
      (12.6), then persists the Project_Config.
    """
    if command is None or not command.strip():
        return

    project_config = config_store.get_project_config()
    history = project_config.get("history")
    if not isinstance(history, list):
        history = []

    # No consecutive duplicate of the most recent entry (Req 12.7).
    if history and history[0] == command:
        return

    history.insert(0, command)
    project_config["history"] = history[:MAX_HISTORY_ITEMS]
    config_store.save_project_config(project_config)
