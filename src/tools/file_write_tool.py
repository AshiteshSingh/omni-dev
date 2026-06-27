"""
FileWriteTool - Python conversion of scratch_repo/src/tools/FileWriteTool

Creates a completely new file and writes content to it.
For existing files, FileEditTool should be used instead.
"""
import os
from typing import Any, Dict

from .base_tool import BaseTool


class FileWriteTool(BaseTool):
    """
    Write content to a new file.
    Python port of scratch_repo FileWriteTool.
    """

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a completely new file and write content to it. "
            "For existing files, use edit_file instead to save tokens and preserve context. "
            "Parent directories will be created automatically if they don't exist."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the new file to create.",
            },
            "content": {
                "type": "string",
                "description": "The full text content to write to the file.",
            },
        }

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return True

    async def call(self, path: str, content: str) -> str:
        """Write content to a new file."""
        try:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            lines = content.count("\n") + 1
            size_kb = round(len(content.encode("utf-8")) / 1024, 1)
            return f"✅ Successfully wrote {lines} lines ({size_kb}KB) to: {path}"
        except PermissionError:
            return f"Error: Permission denied writing to {path}"
        except Exception as e:
            return f"Error writing file: {e}"
