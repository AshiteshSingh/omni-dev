"""staleness.py - Detect when the Knowledge_Graph no longer reflects the tree.

Staleness is determined by comparing on-disk file modification times against the
last index time recorded in ``GraphMeta``. The module computes the changed-file
set (and deleted files), a boolean ``is_stale`` summary, and an ``annotate``
helper that attaches a user-facing notice to a retrieval result when stale.
"""

from __future__ import annotations

import os
from typing import Optional, Set, Tuple

#: User-facing notice attached to stale results.
STALE_NOTICE = "⚠️ knowledge graph is stale; run /graph build"


def changed_files(project_root, meta, excluded: Set[str]) -> Tuple[Set[str], Set[str]]:
    """Return ``(changed, deleted)`` relative-path sets.

    ``changed`` is the set of currently indexable source files whose modification
    time is later than ``meta.last_index_time`` (this naturally includes files
    created after the last index). ``deleted`` is the set of previously indexed
    files that no longer exist on disk.
    """
    # Imported lazily to avoid an import cycle (builder imports this module).
    from .builder import iter_indexable_files

    changed: Set[str] = set()
    present: Set[str] = set()

    last_index_time = getattr(meta, "last_index_time", 0.0) or 0.0

    for rel, _abspath, mtime, _ext in iter_indexable_files(str(project_root), excluded):
        present.add(rel)
        if mtime > last_index_time:
            changed.add(rel)

    indexed = getattr(meta, "indexed_files", {}) or {}
    deleted = {rel for rel in indexed if rel not in present}

    return changed, deleted


def is_stale(project_root, meta, excluded: Set[str]) -> bool:
    """Return True when the graph is out of date relative to the working tree.

    A store that needs a reindex (missing/corrupt) is considered stale. Otherwise
    staleness holds when any source file changed or any indexed file was deleted.
    """
    if getattr(meta, "needs_reindex", False):
        return True
    changed, deleted = changed_files(project_root, meta, excluded)
    return bool(changed or deleted)


def annotate(result, stale: bool):
    """Attach a staleness notice to ``result`` when ``stale`` is True.

    Sets ``result.stale = True`` and prepends :data:`STALE_NOTICE` to
    ``result.notice`` (preserving any existing notice). Returns ``result`` for
    convenient chaining. No-op when not stale.
    """
    if not stale:
        return result

    try:
        result.stale = True
        existing = getattr(result, "notice", None)
        if existing:
            if STALE_NOTICE not in existing:
                result.notice = f"{STALE_NOTICE}\n{existing}"
        else:
            result.notice = STALE_NOTICE
    except Exception:
        # Annotation is advisory; never let it break a result.
        pass
    return result
