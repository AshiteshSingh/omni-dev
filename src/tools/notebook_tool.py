"""
NotebookTool - Python conversion of scratch_repo NotebookReadTool and NotebookEditTool.

Read and edit Jupyter notebook files.
"""
import json
import os
from typing import Any, Dict, List, Optional

from .base_tool import BaseTool


def read_notebook_cells(path: str) -> List[Dict]:
    """Read cells from a Jupyter notebook."""
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    return nb.get("cells", [])


def format_cell(cell: Dict, index: int) -> str:
    """Format a notebook cell for display."""
    cell_type = cell.get("cell_type", "unknown")
    source = "".join(cell.get("source", []))
    outputs = cell.get("outputs", [])

    lines = [f"[Cell {index + 1}] ({cell_type})"]
    lines.append("```")
    lines.append(source)
    lines.append("```")

    if outputs:
        lines.append("Output:")
        for out in outputs[:3]:  # Limit to first 3 outputs
            out_type = out.get("output_type", "")
            if out_type in ("stream", "display_data", "execute_result"):
                text = out.get("text", out.get("data", {}).get("text/plain", ""))
                if isinstance(text, list):
                    text = "".join(text)
                lines.append(str(text)[:500])  # Truncate long output

    return "\n".join(lines)


class NotebookReadTool(BaseTool):
    """
    Read a Jupyter notebook (.ipynb) file.
    Python port of scratch_repo NotebookReadTool.
    """

    @property
    def name(self) -> str:
        return "read_notebook"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a Jupyter notebook (.ipynb) file. "
            "Returns all cells with their type (code/markdown) and outputs. "
            "Use read_file for non-notebook files."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the .ipynb notebook file.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    async def call(self, path: str) -> str:
        """Read and display notebook contents."""
        if not os.path.exists(path):
            return f"Error: Notebook does not exist: {path}"
        if not path.endswith(".ipynb"):
            return f"Error: {path} is not a Jupyter notebook (.ipynb) file."

        try:
            cells = read_notebook_cells(path)
            if not cells:
                return f"Notebook {path} has no cells."

            formatted = [f"Notebook: {path} ({len(cells)} cells)\n"]
            for i, cell in enumerate(cells):
                formatted.append(format_cell(cell, i))
                formatted.append("")  # blank line between cells

            return "\n".join(formatted)

        except json.JSONDecodeError:
            return f"Error: {path} is not a valid JSON/notebook file."
        except Exception as e:
            return f"Error reading notebook: {e}"


class NotebookEditTool(BaseTool):
    """
    Edit a Jupyter notebook by replacing a cell's source.
    Python port of scratch_repo NotebookEditTool.
    """

    @property
    def name(self) -> str:
        return "edit_notebook"

    @property
    def description(self) -> str:
        return (
            "Edit a cell in a Jupyter notebook (.ipynb) file. "
            "Specify the cell index (0-based) and the new source content. "
            "Use read_notebook to see cell indices first."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the .ipynb notebook file.",
            },
            "cell_index": {
                "type": "integer",
                "description": "The 0-based index of the cell to edit.",
            },
            "new_source": {
                "type": "string",
                "description": "The new source content for the cell.",
            },
            "cell_type": {
                "type": "string",
                "description": "Optional. Change the cell type: 'code' or 'markdown'.",
            },
        }

    @property
    def required_params(self):
        return ["path", "cell_index", "new_source"]

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return True

    async def call(
        self,
        path: str,
        cell_index: int,
        new_source: str,
        cell_type: Optional[str] = None,
    ) -> str:
        """Edit a notebook cell."""
        if not os.path.exists(path):
            return f"Error: Notebook does not exist: {path}"
        if not path.endswith(".ipynb"):
            return f"Error: {path} is not a .ipynb file."

        try:
            with open(path, "r", encoding="utf-8") as f:
                nb = json.load(f)

            cells = nb.get("cells", [])
            if cell_index < 0 or cell_index >= len(cells):
                return f"Error: Cell index {cell_index} out of range (notebook has {len(cells)} cells)."

            # Update the cell
            cell = cells[cell_index]
            cell["source"] = list(new_source)  # Store as list of chars (notebook format)
            # Clear outputs when editing a code cell
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            if cell_type in ("code", "markdown"):
                cell["cell_type"] = cell_type

            with open(path, "w", encoding="utf-8") as f:
                json.dump(nb, f, indent=1)

            return f"✅ Updated cell {cell_index} in {path}"

        except json.JSONDecodeError:
            return f"Error: {path} is not a valid notebook file."
        except Exception as e:
            return f"Error editing notebook: {e}"
