"""store.py - Dependency-free local persistence for the Knowledge_Graph.

``GraphStore`` reads/writes a single JSON document under the project's
``.cognee_data/`` directory. It mirrors two proven patterns:

- Project-root resolution copies ``simple_memory._get_memory_path()``.
- Atomic writes copy ``config_store._atomic_write()`` (temp file + ``os.replace``).

Every read path is wrapped so a missing, corrupt, or unknown-version file yields
an empty Knowledge_Graph (with ``needs_reindex=True``) rather than raising, and
the corrupt file is never deleted. Saves never raise: they return ``False`` on
any failure so the session can continue.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from .model import Knowledge_Graph

#: On-disk schema version. An unrecognized/missing version is treated as corrupt.
SCHEMA_VERSION = 1

#: Store file name under ``.cognee_data/``.
STORE_FILENAME = "knowledge_graph.json"


@dataclass
class GraphMeta:
    """Last-index metadata persisted alongside the graph.

    Attributes:
        last_index_time: Epoch seconds of the last successful reindex.
        indexed_files: Map of relative path -> mtime captured at index time.
        partial: True when an Index_Budget halted indexing early.
        needs_reindex: True when loaded from a missing/corrupt store.
    """

    last_index_time: float = 0.0
    indexed_files: Dict[str, float] = field(default_factory=dict)
    partial: bool = False
    needs_reindex: bool = False


def _resolve_store_path(project_root: Optional[str] = None) -> Path:
    """Resolve ``.cognee_data/knowledge_graph.json``.

    When ``project_root`` is given, the store lives under it directly. Otherwise
    walk up from the cwd to find the project root marker (``omni_dev.py`` or
    ``.env``), mirroring ``simple_memory._get_memory_path()``.
    """
    if project_root is not None:
        data_dir = Path(project_root) / ".cognee_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / STORE_FILENAME

    cwd = Path(os.getcwd())
    check = cwd
    for _ in range(5):
        if (check / "omni_dev.py").exists() or (check / ".env").exists():
            data_dir = check / ".cognee_data"
            data_dir.mkdir(exist_ok=True)
            return data_dir / STORE_FILENAME
        check = check.parent

    data_dir = cwd / ".cognee_data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / STORE_FILENAME


class GraphStore:
    """Local JSON persistence layer for the Knowledge_Graph."""

    def __init__(self, project_root: Optional[str] = None):
        self._path = _resolve_store_path(project_root)

    def path(self) -> Path:
        """Return the resolved store file path."""
        return self._path

    def exists(self) -> bool:
        """Return True when the store file is present on disk."""
        try:
            return self._path.exists()
        except OSError:
            return False

    def save(
        self,
        graph: Knowledge_Graph,
        last_index_time: float,
        indexed_files: Optional[Dict[str, float]] = None,
        partial: bool = False,
    ) -> bool:
        """Persist the graph + metadata atomically. Returns True on success.

        Never raises: any I/O or serialization failure returns ``False`` so the
        caller can continue. The existing file is left untouched on failure.
        """
        document = {
            "version": SCHEMA_VERSION,
            "meta": {
                "last_index_time": float(last_index_time),
                "indexed_files": dict(indexed_files or {}),
                "partial": bool(partial),
            },
            **graph.to_dict(),
        }
        try:
            self._atomic_write(document)
            return True
        except Exception:
            return False

    def load(self) -> Tuple[Knowledge_Graph, GraphMeta]:
        """Load the graph + metadata.

        Missing file -> (empty graph, meta with ``needs_reindex=True``).
        Corrupt / invalid / unknown-version content -> (empty graph,
        ``needs_reindex=True``) WITHOUT raising and WITHOUT deleting the file.
        """
        empty = (Knowledge_Graph(), GraphMeta(needs_reindex=True))

        if not self.exists():
            return empty

        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return empty

        if not isinstance(data, dict):
            return empty
        if data.get("version") != SCHEMA_VERSION:
            return empty

        try:
            graph = Knowledge_Graph.from_dict(data)
            meta = self._meta_from_dict(data.get("meta"))
            return (graph, meta)
        except Exception:
            return empty

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _meta_from_dict(raw) -> GraphMeta:
        """Build GraphMeta from the persisted ``meta`` mapping, defensively."""
        if not isinstance(raw, dict):
            return GraphMeta()
        try:
            last_index_time = float(raw.get("last_index_time", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_index_time = 0.0
        indexed = raw.get("indexed_files", {})
        indexed_files: Dict[str, float] = {}
        if isinstance(indexed, dict):
            for k, v in indexed.items():
                try:
                    indexed_files[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        return GraphMeta(
            last_index_time=last_index_time,
            indexed_files=indexed_files,
            partial=bool(raw.get("partial", False)),
            needs_reindex=False,
        )

    def _atomic_write(self, document: dict) -> None:
        """Write ``document`` as JSON atomically (temp file + ``os.replace``)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, ensure_ascii=False, indent=2)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".kg.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_path, str(self._path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
