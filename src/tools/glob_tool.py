"""
GlobTool - Python conversion of scratch_repo/src/tools/GlobTool/GlobTool.tsx

Find files matching a glob pattern.
"""
import os
import glob as glob_module
import time
from typing import Any, Dict, List, Optional, Tuple

from .base_tool import BaseTool

MAX_RESULTS = 100


def glob_files(
    pattern: str,
    base_path: str,
    limit: int = MAX_RESULTS,
) -> Tuple[List[str], bool]:
    """
    Run a glob pattern search and return matching files.
    Mirrors glob utility from scratch_repo.
    Returns (files, truncated).
    """
    search_path = os.path.join(base_path, "**", pattern) if not os.path.isabs(pattern) else pattern
    # Also try direct pattern
    matches = glob_module.glob(search_path, recursive=True)
    if not matches:
        # Fallback: try the pattern as-is under base_path
        matches = glob_module.glob(os.path.join(base_path, pattern), recursive=True)

    # Filter out hidden/ignored directories
    filtered = []
    for m in matches:
        normalized = m.replace("\\", "/")
        if any(x in normalized for x in ["/.git/", "/node_modules/", "/venv/", "/__pycache__/"]):
            continue
        filtered.append(m)

    # Sort by modification time (most recently modified first)
    try:
        filtered.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    except Exception:
        filtered.sort()

    truncated = len(filtered) > limit
    return filtered[:limit], truncated


class GlobTool(BaseTool):
    """
    Find files matching a glob pattern.
    Python port of scratch_repo GlobTool.
    """

    @property
    def name(self) -> str:
        return "glob_files"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern (e.g., '*.py', '**/*.ts', 'src/**/*.json'). "
            "Results are sorted by most recently modified. "
            "Use this to discover files before reading them. "
            "Returns up to 100 results."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against (e.g., '*.py', '**/*.ts').",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. Defaults to current working directory.",
            },
        }

    @property
    def required_params(self):
        return ["pattern"]

    def is_read_only(self) -> bool:
        return True

    async def call(self, pattern: str, path: Optional[str] = None) -> str:
        """Search for files matching the glob pattern."""
        start = time.time()
        base = path or os.getcwd()

        try:
            files, truncated = glob_files(pattern, base)
            duration_ms = round((time.time() - start) * 1000)

            if not files:
                return f"No files found matching pattern: '{pattern}' in {base}"

            result = f"Found {len(files)} file{'s' if len(files) != 1 else ''} ({duration_ms}ms):\n"
            result += "\n".join(files)

            if truncated:
                result += "\n(Results truncated. Use a more specific pattern.)"

            return result

        except Exception as e:
            return f"Error searching files: {e}"
