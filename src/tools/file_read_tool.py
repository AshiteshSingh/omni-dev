"""
FileReadTool - Python conversion of scratch_repo/src/tools/FileReadTool/FileReadTool.tsx

Enhanced version with:
- Line offset/limit support (read specific portions of large files)
- Image file detection and base64 encoding
- File size limits with helpful error messages
- Line number rendering (mirrors addLineNumbers utility)
"""
import os
import base64
from typing import Any, Dict, Optional

from .base_tool import BaseTool

MAX_OUTPUT_BYTES = 256 * 1024  # 0.25MB
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def add_line_numbers(content: str, start_line: int = 1) -> str:
    """
    Add line numbers to content.
    Mirrors addLineNumbers utility from scratch_repo.
    """
    lines = content.split("\n")
    width = len(str(start_line + len(lines) - 1))
    return "\n".join(
        f"{str(i + start_line).rjust(width)}: {line}"
        for i, line in enumerate(lines)
    )


class FileReadTool(BaseTool):
    """
    Read the contents of a local file.
    Python port of scratch_repo FileReadTool.
    """

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a local file. "
            "Supports text files (returns content with line numbers) and image files (returns base64). "
            "Use offset and limit to read specific portions of large files. "
            "NEVER guess file paths. Use list_dir or glob_files to verify paths first."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from (1-indexed). Only use for large files.",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only use for large files.",
            },
        }

    @property
    def required_params(self):
        return ["path"]

    def is_read_only(self) -> bool:
        return True

    async def call(self, path: str, offset: int = 1, limit: Optional[int] = None) -> str:
        """Read the file and return its content."""
        try:
            if not os.path.exists(path):
                # Try to find similar file
                dir_name = os.path.dirname(path) or "."
                base_name = os.path.basename(path)
                similar = self._find_similar_file(dir_name, base_name)
                msg = f"File does not exist: {path}"
                if similar:
                    msg += f"\nDid you mean: {similar}?"
                return msg

            # Check if image
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                return self._read_image(path, ext)

            # Check file size
            size = os.path.getsize(path)
            if size > MAX_OUTPUT_BYTES and offset == 1 and limit is None:
                kb = round(size / 1024)
                max_kb = round(MAX_OUTPUT_BYTES / 1024)
                return (
                    f"File too large ({kb}KB > {max_kb}KB limit). "
                    "Use offset and limit parameters to read specific portions, "
                    "or use grep_search to find specific content."
                )

            # Read text content
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            start_idx = max(0, offset - 1)
            if limit is not None:
                end_idx = start_idx + limit
            else:
                end_idx = total_lines

            selected_lines = all_lines[start_idx:end_idx]
            content = "".join(selected_lines)

            # Add line numbers
            result = add_line_numbers(content.rstrip(), start_line=start_idx + 1)

            header = f"File: {path} ({total_lines} total lines)"
            if offset > 1 or limit is not None:
                header += f" [showing lines {start_idx+1}-{min(end_idx, total_lines)}]"
            return f"{header}\n\n{result}"

        except UnicodeDecodeError:
            return f"Error: File {path} is a binary file and cannot be read as text."
        except PermissionError:
            return f"Error: Permission denied reading {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_image(self, path: str, ext: str) -> str:
        """Read image file and return base64 representation."""
        try:
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("utf-8")
            mime = f"image/{ext[1:]}"
            return f"[IMAGE: {path}]\nMIME type: {mime}\nBase64 data: {b64[:100]}... (truncated for display)"
        except Exception as e:
            return f"Error reading image: {e}"

    def _find_similar_file(self, directory: str, filename: str) -> Optional[str]:
        """Find a file with a similar name (different extension)."""
        try:
            base = os.path.splitext(filename)[0]
            for f in os.listdir(directory):
                if os.path.splitext(f)[0] == base and f != filename:
                    return os.path.join(directory, f)
        except Exception:
            pass
        return None
