"""
GrepTool - Python conversion of scratch_repo/src/tools/GrepTool/GrepTool.tsx

Search codebase using regex patterns.
Enhanced version using ripgrep if available, falling back to Python's re module.
"""
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_tool import BaseTool

MAX_RESULTS = 100
IGNORED_DIRS = {".git", "node_modules", "venv", "__pycache__", ".venv", "dist", "build"}


def ripgrep_search(pattern: str, path: str, include: Optional[str] = None) -> List[str]:
    """Try to use ripgrep for fast searching."""
    args = ["rg", "-li", pattern]
    if include:
        args.extend(["--glob", include])
    args.append(path)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if result.returncode in (0, 1):  # 0=found, 1=not found
            return result.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None  # type: ignore


def python_search(pattern: str, base_path: str, include: Optional[str] = None) -> List[str]:
    """
    Python fallback search using re module.
    Returns list of matching file paths.
    """
    matches = []
    include_ext = None
    if include:
        # Convert glob pattern like *.py to extension check
        if include.startswith("*."):
            include_ext = "." + include[2:]

    for root, dirs, files in os.walk(base_path):
        # Skip ignored directories (in-place modification)
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        for filename in files:
            if include_ext and not filename.endswith(include_ext):
                continue
            filepath = os.path.join(root, filename)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if re.search(pattern, content):
                    matches.append(filepath)
                    if len(matches) >= MAX_RESULTS:
                        return matches
            except (PermissionError, OSError):
                continue

    return matches


class GrepTool(BaseTool):
    """
    Search codebase for files containing a regex pattern.
    Python port of scratch_repo GrepTool.
    Uses ripgrep if available, falls back to Python re.
    """

    @property
    def name(self) -> str:
        return "search_codebase"

    @property
    def description(self) -> str:
        return (
            "Search the codebase for files containing a regex pattern (like grep). "
            "Returns file paths sorted by most recently modified. "
            "Use this to find where functions, variables, or strings are defined. "
            "Use glob_files if you want to search by filename pattern instead."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents.",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. Defaults to current working directory.",
            },
            "include": {
                "type": "string",
                "description": "File pattern filter (e.g., '*.py', '*.{ts,tsx}'). Optional.",
            },
        }

    @property
    def required_params(self):
        return ["pattern"]

    def is_read_only(self) -> bool:
        return True

    async def call(self, pattern: str, path: Optional[str] = None, include: Optional[str] = None) -> str:
        """Search for files matching the regex pattern."""
        start = time.time()
        base = path or os.getcwd()

        try:
            # Try ripgrep first
            files = ripgrep_search(pattern, base, include)
            if files is None:
                # Fall back to Python search
                files = python_search(pattern, base, include)

            duration_ms = round((time.time() - start) * 1000)

            if not files:
                return f"No files found containing pattern: '{pattern}'"

            # Sort by modification time
            try:
                files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
            except Exception:
                files.sort()

            truncated = len(files) > MAX_RESULTS
            shown = files[:MAX_RESULTS]

            result = f"Found {len(files)} file{'s' if len(files) != 1 else ''} ({duration_ms}ms):\n"
            result += "\n".join(shown)
            if truncated:
                result += "\n(Results truncated. Use a more specific path or pattern.)"

            return result

        except re.error as e:
            return f"Invalid regex pattern: {e}"
        except Exception as e:
            return f"Error searching codebase: {e}"
