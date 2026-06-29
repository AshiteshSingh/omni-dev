"""
ui_state.py - Shared UI coordination between the REPL and tools.

The interactive interface shows a live "Generating..." spinner (a Rich
``console.status``) while the agent works. If a tool needs to read input from
the user mid-task (e.g. ``ask_user``), that live spinner keeps refreshing the
terminal and fights the input prompt, so keystrokes get clobbered.

This module exposes a tiny, thread-safe registry of the currently-active status
spinner plus pause/resume helpers so any code path that must read user input can
temporarily stop the spinner and restart it afterwards — without needing a
direct reference to the interface internals.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Optional

_lock = threading.Lock()
_active_status: Optional[Any] = None


def set_active_status(status: Any) -> None:
    """Register the currently-active spinner/status (or ``None`` to clear)."""
    global _active_status
    with _lock:
        _active_status = status


def clear_active_status() -> None:
    """Clear the registered active spinner/status."""
    set_active_status(None)


def pause_status() -> Optional[Any]:
    """Stop the active spinner if any; return it so it can be resumed later."""
    with _lock:
        status = _active_status
    if status is not None:
        try:
            status.stop()
        except Exception:
            pass
    return status


def resume_status(status: Optional[Any]) -> None:
    """Restart a spinner previously returned by :func:`pause_status`."""
    if status is not None:
        try:
            status.start()
        except Exception:
            pass


@contextlib.contextmanager
def input_guard():
    """Context manager that pauses the active spinner for the duration of input."""
    status = pause_status()
    try:
        yield
    finally:
        resume_status(status)
