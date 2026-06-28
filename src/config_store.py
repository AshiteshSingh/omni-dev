"""Persistent configuration store for Omni-Dev.

Mirrors the reference TypeScript single-file model (``scratch_repo/src/utils/config.ts``):
a single global JSON file holds cross-project (global) settings plus a ``projects``
map keyed by each project's absolute path, where each value is a Project_Config.

Behavior contract (Requirement 9):
- Loading a missing file returns Config_Defaults without raising (9.4).
- Loading an unparseable/corrupt file returns Config_Defaults without raising and
  WITHOUT deleting the existing file (9.5).
- Loading a file that omits known keys supplies defaults for the missing keys while
  preserving the stored values for present keys (shallow merge) (9.6).
- Writes are atomic: content is written to a temp file then ``os.replace``d into place.
- The config directory is created on save.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

#: Directory holding the single global config file (and, by convention, transcripts).
GLOBAL_DIR: Path = (
    Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".omni-dev"
)

#: The single global config file. It holds global keys plus a ``projects`` map
#: keyed by absolute project path, each value a Project_Config.
GLOBAL_FILE: Path = GLOBAL_DIR / "config.json"


# ---------------------------------------------------------------------------
# Config defaults (Config_Defaults)
# ---------------------------------------------------------------------------

#: Default global configuration applied when the file is absent, unreadable, or
#: omits a known key.
DEFAULT_GLOBAL_CONFIG: Dict[str, Any] = {
    "activeModel": None,
    "numStartups": 0,
    "verbose": False,
    "theme": "omni-dark",
    "costThreshold": 5.0,
    "tokenWarningThreshold": 1000000,
    "costThresholdAcknowledged": False,
    "ollamaApiBase": None,
    "terminalSetup": None,
    "mcpServers": {},
    "projects": {},
}

#: Default per-project configuration.
DEFAULT_PROJECT_CONFIG: Dict[str, Any] = {
    "activeModel": None,
    "allowedTools": [],
    "history": [],
    "hasTrustDialogAccepted": False,
    "mcpServers": {},
    "context": {},
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_global_file() -> Path:
    """Resolve the global config file path at call time.

    Resolving lazily (rather than caching the module-level constant) lets tests
    redirect the home directory via ``USERPROFILE``/``HOME`` after import.
    """
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".omni-dev" / "config.json"


def _merge_with_defaults(stored: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge ``stored`` over a deep copy of ``defaults``.

    Present keys keep their stored values; missing keys receive defaults (9.6).
    """
    merged = copy.deepcopy(defaults)
    if isinstance(stored, dict):
        merged.update(stored)
    return merged


def _load_config(file: Path, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Load a JSON config file, falling back to defaults safely.

    Returns a deep copy of ``defaults`` when the file is missing or cannot be
    parsed. Never raises and never deletes the existing file (9.4, 9.5).
    """
    if not file.exists():
        return copy.deepcopy(defaults)
    try:
        with open(file, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError):
        # Corrupt/unreadable file: fall back to defaults WITHOUT deleting it.
        return copy.deepcopy(defaults)

    if not isinstance(parsed, dict):
        # Not an object at the top level; treat as corrupt -> defaults.
        return copy.deepcopy(defaults)

    return _merge_with_defaults(parsed, defaults)


def _atomic_write(file: Path, data: Dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``file`` atomically (temp file + ``os.replace``)."""
    file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    # Write to a temp file in the same directory so os.replace is atomic on all
    # platforms (rename across filesystems is not).
    fd, tmp_path = tempfile.mkstemp(
        prefix=".config.", suffix=".tmp", dir=str(file.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, str(file))
    except BaseException:
        # Clean up the temp file on any failure; do not touch the existing file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------

def get_global_config() -> Dict[str, Any]:
    """Return the Global_Config, applying Config_Defaults for any missing keys."""
    return _load_config(_resolve_global_file(), DEFAULT_GLOBAL_CONFIG)


def save_global_config(cfg: Dict[str, Any]) -> None:
    """Persist the Global_Config atomically, creating the config dir as needed."""
    _atomic_write(_resolve_global_file(), cfg)


# ---------------------------------------------------------------------------
# Project config (keyed by absolute path inside the global file's `projects` map)
# ---------------------------------------------------------------------------

def _abspath(path: Optional[str]) -> str:
    """Resolve ``path`` (defaulting to the current working directory) to an abspath."""
    return os.path.abspath(path if path is not None else os.getcwd())


def get_project_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the Project_Config for ``path`` (defaults to cwd), keyed by abspath.

    Missing keys receive Config_Defaults; an absent project entry yields the full
    default project config.
    """
    key = _abspath(path)
    global_config = get_global_config()
    projects = global_config.get("projects")
    stored = projects.get(key) if isinstance(projects, dict) else None
    if not isinstance(stored, dict):
        return copy.deepcopy(DEFAULT_PROJECT_CONFIG)
    return _merge_with_defaults(stored, DEFAULT_PROJECT_CONFIG)


def save_project_config(cfg: Dict[str, Any], path: Optional[str] = None) -> None:
    """Persist the Project_Config for ``path`` (defaults to cwd) into the global file.

    Reads the existing global config, updates the ``projects`` entry keyed by the
    project's absolute path, and writes the whole global file back atomically.
    """
    key = _abspath(path)
    global_config = get_global_config()
    projects = global_config.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    projects[key] = cfg
    global_config["projects"] = projects
    save_global_config(global_config)
