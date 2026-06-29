"""config.py - Graph configuration (Index_Budget and traversal/result bounds).

Reads settings from ``src.config_store`` with project config preferred, then
global config, then hard-coded defaults, mirroring the defensive reads in
``cost_tracker``. Any missing or invalid value falls back to its default without
raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# Defaults (see design "Configuration" table).
DEFAULT_MAX_FILES = 5000          # graphMaxFiles  (Index_Budget file max)
DEFAULT_MAX_SECONDS = 30.0        # graphMaxSeconds(Index_Budget duration max)
DEFAULT_MAX_DEPTH = 2             # graphMaxDepth  (traversal edge depth bound)
DEFAULT_RESULT_LIMIT = 25         # graphResultLimit (retrieval node cap)


@dataclass
class GraphConfig:
    """Resolved graph configuration."""

    max_files: int = DEFAULT_MAX_FILES
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_depth: int = DEFAULT_MAX_DEPTH
    result_limit: int = DEFAULT_RESULT_LIMIT


def _read_setting(
    key: str,
    default: Any,
    cast: Callable[[Any], Any],
    project_path: Optional[str] = None,
) -> Any:
    """Read ``key`` from project config, then global config, then ``default``.

    Returns the first value that casts cleanly. Any error (config unavailable,
    missing key, invalid value) falls through to the default without raising.
    """
    try:
        from src.config_store import get_project_config, get_global_config

        sources = []
        try:
            sources.append(get_project_config(project_path))
        except Exception:
            pass
        try:
            sources.append(get_global_config())
        except Exception:
            pass

        for cfg in sources:
            if not isinstance(cfg, dict):
                continue
            value = cfg.get(key)
            if value is None:
                continue
            try:
                return cast(value)
            except (TypeError, ValueError):
                continue
        return default
    except Exception:
        return default


def get_graph_config(project_path: Optional[str] = None) -> GraphConfig:
    """Return the resolved :class:`GraphConfig`.

    Each bound is read defensively; invalid or missing values use the default.
    Non-positive numeric values fall back to defaults as a safety guard.
    """
    max_files = _read_setting("graphMaxFiles", DEFAULT_MAX_FILES, int, project_path)
    if not isinstance(max_files, int) or max_files <= 0:
        max_files = DEFAULT_MAX_FILES

    max_seconds = _read_setting("graphMaxSeconds", DEFAULT_MAX_SECONDS, float, project_path)
    if not isinstance(max_seconds, float) or max_seconds <= 0:
        max_seconds = DEFAULT_MAX_SECONDS

    max_depth = _read_setting("graphMaxDepth", DEFAULT_MAX_DEPTH, int, project_path)
    if not isinstance(max_depth, int) or max_depth < 0:
        max_depth = DEFAULT_MAX_DEPTH

    result_limit = _read_setting("graphResultLimit", DEFAULT_RESULT_LIMIT, int, project_path)
    if not isinstance(result_limit, int) or result_limit <= 0:
        result_limit = DEFAULT_RESULT_LIMIT

    return GraphConfig(
        max_files=max_files,
        max_seconds=max_seconds,
        max_depth=max_depth,
        result_limit=result_limit,
    )
