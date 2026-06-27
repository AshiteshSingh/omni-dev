"""
FileEditTool - Python conversion of scratch_repo/src/tools/FileEditTool/FileEditTool.tsx

Enhanced version with:
- old_string / new_string pattern (from scratch_repo)
- Validation that old_string appears exactly once
- File existence checks
- Creates new files if old_string is empty
- Post-edit snippet display with line numbers
"""
import os
from typing import Any, Dict, Optional

from .base_tool import BaseTool
from .file_read_tool import add_line_numbers

N_LINES_SNIPPET = 4


def get_snippet(original: str, old_string: str, new_string: str) -> tuple[str, int]:
    """
    Get a snippet of the file around the edit.
    Mirrors getSnippet from scratch_repo FileEditTool.
    """
    before = original.split(old_string)[0] if old_string else ""
    replacement_line = len(before.split("\n")) - 1
    new_file_lines = original.replace(old_string, new_string, 1).split("\n") if old_string else new_string.split("\n")
    start_line = max(0, replacement_line - N_LINES_SNIPPET)
    end_line = replacement_line + N_LINES_SNIPPET + len(new_string.split("\n"))
    snippet_lines = new_file_lines[start_line: end_line + 1]
    return "\n".join(snippet_lines), start_line + 1


class FileEditTool(BaseTool):
    """
    Edit an existing file by replacing a specific string.
    Python port of scratch_repo FileEditTool.
    Uses old_string/new_string pattern with strict single-match validation.
    """

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit an existing file by replacing a specific block of text. "
            "The old_string must appear EXACTLY ONCE in the file — add more context lines "
            "if there are multiple matches. "
            "If old_string is empty and the file does not exist, a new file is created. "
            "ALWAYS read the file first before editing it to get exact content."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to replace. Must match exactly including whitespace and indentation. Use empty string to create a new file.",
            },
            "new_string": {
                "type": "string",
                "description": "The new text to insert in place of old_string.",
            },
        }

    @property
    def required_params(self):
        return ["file_path", "old_string", "new_string"]

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return True

    async def call(self, file_path: str, old_string: str, new_string: str) -> str:
        """Apply the edit to the file."""
        try:
            full_path = os.path.abspath(file_path)

            # Case 1: Create new file
            if old_string == "":
                if os.path.exists(full_path):
                    return f"Error: Cannot create file — '{file_path}' already exists. Use a non-empty old_string to edit it."
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_string)
                return f"✅ Created new file: {file_path}"

            # Case 2: Edit existing file
            if not os.path.exists(full_path):
                return f"Error: File does not exist: {file_path}. Use write_file to create it, or verify the path with list_dir."

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if old_string == new_string:
                return "Error: old_string and new_string are identical — no changes to make."

            # Validate exactly one match
            match_count = content.count(old_string)
            if match_count == 0:
                return (
                    f"Error: old_string not found in {file_path}.\n"
                    "Make sure your indentation and line endings match exactly. "
                    "Read the file first to verify the exact content."
                )
            if match_count > 1:
                return (
                    f"Error: Found {match_count} matches of old_string in {file_path}. "
                    "For safety, only one occurrence can be replaced at a time. "
                    "Add more context lines around your edit to make it unique."
                )

            # Apply the edit
            original_content = content
            new_content = content.replace(old_string, new_string, 1)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            # Show a snippet of the result
            snippet, start_line = get_snippet(original_content, old_string, new_string)
            numbered_snippet = add_line_numbers(snippet, start_line=start_line)
            return (
                f"✅ File updated: {file_path}\n\n"
                f"Here is a snippet of the edited file:\n"
                f"```\n{numbered_snippet}\n```"
            )

        except PermissionError:
            return f"Error: Permission denied editing {file_path}"
        except Exception as e:
            return f"Error editing file: {e}"
