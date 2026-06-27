"""
LSTool - Python conversion of scratch_repo/src/tools/lsTool/lsTool.ts

List directory contents with tree-like output.
"""
import os
import time
from typing import Any, Dict, List, Optional

from .base_tool import BaseTool

IGNORED_DIRS = {".git", "node_modules", "venv", "__pycache__", ".venv", "dist", "build", ".next"}
MAX_FILES = 500


def build_tree(
    path: str,
    prefix: str = "",
    max_files: int = MAX_FILES,
    _count: List[int] = None,
) -> str:
    """
    Build a tree representation of a directory.
    Mirrors LSTool behavior from scratch_repo.
    """
    if _count is None:
        _count = [0]

    if _count[0] >= max_files:
        return ""

    lines = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return f"{prefix}[Permission denied]\n"

    for i, entry in enumerate(entries):
        if _count[0] >= max_files:
            lines.append(f"{prefix}... (truncated)")
            break

        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if entry.is_dir():
            if entry.name in IGNORED_DIRS:
                continue
            lines.append(f"{prefix}{connector}{entry.name}/")
            _count[0] += 1
            subtree = build_tree(entry.path, child_prefix, max_files, _count)
            if subtree:
                lines.append(subtree.rstrip("\n"))
        else:
            try:
                size = entry.stat().st_size
                if size >= 1024 * 1024:
                    size_str = f" ({round(size/1024/1024, 1)}MB)"
                elif size >= 1024:
                    size_str = f" ({round(size/1024, 1)}KB)"
                else:
                    size_str = f" ({size}B)"
            except Exception:
                size_str = ""
            lines.append(f"{prefix}{connector}{entry.name}{size_str}")
            _count[0] += 1

    return "\n".join(lines)


class LSTool(BaseTool):
    """
    List directory contents in a tree format.
    Python port of scratch_repo LSTool.
    """

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List files and directories in a tree format. "
            "Use this to understand the project structure before reading files. "
            "Ignores .git, node_modules, venv, and __pycache__ directories."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Directory path to list. Defaults to current working directory ('.').",
            },
        }

    @property
    def required_params(self):
        return []

    def is_read_only(self) -> bool:
        return True

    async def call(self, path: str = ".") -> str:
        """List directory contents."""
        abs_path = os.path.abspath(path)

        if not os.path.exists(abs_path):
            return f"Error: Path does not exist: {path}"
        if not os.path.isdir(abs_path):
            return f"Error: {path} is not a directory."

        try:
            tree = build_tree(abs_path)
            header = f"{abs_path}\n"
            if tree:
                return header + tree
            else:
                return header + "(empty directory)"
        except Exception as e:
            return f"Error listing directory: {e}"
