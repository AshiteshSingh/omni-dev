"""builder.py - AST-based Reindexer (full + incremental).

Walks the repository, parses Python with the stdlib ``ast`` module, and derives
the Knowledge_Graph nodes and edges:

- one ``file`` node per indexable source file (Excluded_Path descendants pruned);
- ``function``/``class`` nodes with ``defines`` edges from the file;
- ``imports`` edges to ``module`` nodes;
- ``depends-on`` edges to resolved repository files;
- ``calls`` edges to known defined entities (name-based, approximate).

Unparseable files keep a file-only node. Indexing respects the Index_Budget
(file count + wall-clock) and always persists, including empty/partial graphs.
Incremental reindex re-derives only changed files and drops deleted files.
"""

from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .model import Graph_Node, Knowledge_Graph, node_id
from .staleness import changed_files

# Noise-exclusion set from the requirements glossary (kept aligned with
# ``src/context.py:get_directory_structure``).
DEFAULT_EXCLUDED: Set[str] = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next",
}

#: Python source files are fully parsed.
PYTHON_EXTENSIONS: Set[str] = {".py"}

#: Other text/source files get a file-only node (no sub-entities).
OTHER_TEXT_EXTENSIONS: Set[str] = {
    ".md", ".txt", ".rst", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".sh", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css",
}

#: All extensions considered indexable source files.
INDEXABLE_EXTENSIONS: Set[str] = PYTHON_EXTENSIONS | OTHER_TEXT_EXTENSIONS


@dataclass
class FileParseResult:
    """Outcome of parsing a single Python source file."""

    functions: List[Tuple[str, int, int]] = field(default_factory=list)  # (name, ls, le)
    classes: List[Tuple[str, int, int]] = field(default_factory=list)
    import_modules: Set[str] = field(default_factory=set)        # for imports edges
    depends_candidates: Set[str] = field(default_factory=set)    # for depends-on resolution
    calls: List[Tuple[Optional[str], str]] = field(default_factory=list)  # (caller, callee)
    parse_failed: bool = False


@dataclass
class ReindexResult:
    """Summary of a reindex operation."""

    mode: str                      # "full" | "incremental"
    node_count: int = 0
    edge_count: int = 0
    file_count: int = 0
    partial: bool = False
    parse_failures: int = 0
    changed_files: int = 0
    deleted_files: int = 0
    duration_seconds: float = 0.0
    persisted: bool = False
    no_op: bool = False


def _normalize_rel(abspath: str, root: str) -> str:
    """Return the forward-slash relative path of ``abspath`` under ``root``."""
    rel = os.path.relpath(abspath, root)
    return rel.replace(os.sep, "/")


def iter_indexable_files(project_root: str, excluded: Set[str]):
    """Yield ``(rel, abspath, mtime, ext)`` for each indexable file under root.

    Excluded directories are pruned (their descendants never visited). Shared by
    the builder and the staleness detector so both agree on the file universe.
    """
    root = str(project_root)
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in place so descendants are skipped.
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in INDEXABLE_EXTENSIONS:
                continue
            abspath = os.path.join(dirpath, fn)
            try:
                mtime = os.path.getmtime(abspath)
            except OSError:
                continue
            yield _normalize_rel(abspath, root), abspath, mtime, ext


def _callee_name(func_node) -> Optional[str]:
    """Extract a best-effort callee name from a ``Call.func`` node."""
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


class Reindexer:
    """Builds and incrementally updates a Knowledge_Graph from the repository."""

    def __init__(self, project_root, store, config, excluded: Optional[Set[str]] = None):
        self.project_root = os.fspath(project_root)
        self.store = store
        self.config = config
        self.excluded = set(excluded) if excluded is not None else set(DEFAULT_EXCLUDED)

    # -- public API ---------------------------------------------------------

    def full_reindex(self) -> ReindexResult:
        """Walk the repo, build all nodes/edges, enforce Index_Budget, persist.

        Always persists the resulting graph (even empty/partial). After a
        successful persist, best-effort Cognee enrichment is attempted.
        """
        start = time.monotonic()
        graph = Knowledge_Graph()
        indexed_files: Dict[str, float] = {}
        parse_results: Dict[str, Optional[FileParseResult]] = {}
        partial = False
        file_count = 0
        parse_failures = 0

        for rel, abspath, mtime, ext in iter_indexable_files(self.project_root, self.excluded):
            elapsed = time.monotonic() - start
            if file_count >= self.config.max_files or elapsed >= self.config.max_seconds:
                partial = True
                break

            source = self._read_source(abspath) if ext in PYTHON_EXTENSIONS else None
            pr = self._create_file_and_entities(graph, rel, ext, source)
            if pr is not None and pr.parse_failed:
                parse_failures += 1
            parse_results[rel] = pr
            indexed_files[rel] = mtime
            file_count += 1

        # Second pass: relationship edges need global maps.
        module_to_file, defined_entities = self._build_maps(graph)
        for rel, pr in parse_results.items():
            if pr is not None and not pr.parse_failed:
                self._add_relationship_edges(graph, rel, pr, module_to_file, defined_entities)

        last_index_time = time.time()
        persisted = self.store.save(graph, last_index_time, indexed_files, partial)
        self._enrich(graph)

        return ReindexResult(
            mode="full",
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            file_count=file_count,
            partial=partial,
            parse_failures=parse_failures,
            duration_seconds=time.monotonic() - start,
            persisted=persisted,
        )

    def incremental_reindex(self) -> ReindexResult:
        """Re-derive only changed files and drop deleted files.

        Identifies files with ``mtime > last_index_time`` plus previously indexed
        files that no longer exist; removes their derived nodes/edges; re-derives
        surviving changed files; updates ``last_index_time`` and ``indexed_files``;
        persists. No-op on graph data when nothing changed.
        """
        start = time.monotonic()
        graph, meta = self.store.load()

        if meta.needs_reindex:
            # Nothing usable on disk; fall back to a full reindex.
            return self.full_reindex()

        changed, deleted = changed_files(self.project_root, meta, self.excluded)

        if not changed and not deleted:
            # Nothing changed: leave graph data untouched, refresh the index time.
            last_index_time = time.time()
            persisted = self.store.save(
                graph, last_index_time, meta.indexed_files, meta.partial
            )
            return ReindexResult(
                mode="incremental",
                node_count=len(graph.nodes),
                edge_count=len(graph.edges),
                file_count=len(meta.indexed_files),
                partial=meta.partial,
                duration_seconds=time.monotonic() - start,
                persisted=persisted,
                no_op=True,
            )

        indexed_files: Dict[str, float] = dict(meta.indexed_files)

        # Remove derived nodes/edges for every changed and deleted file.
        for rel in (changed | deleted):
            graph.remove_nodes_for_file(rel)
        for rel in deleted:
            indexed_files.pop(rel, None)

        # Re-derive surviving changed files.
        parse_results: Dict[str, Optional[FileParseResult]] = {}
        parse_failures = 0
        for rel in changed:
            abspath = os.path.join(self.project_root, *rel.split("/"))
            if not os.path.exists(abspath):
                # Disappeared between detection and derivation; treat as deleted.
                indexed_files.pop(rel, None)
                continue
            ext = os.path.splitext(rel)[1].lower()
            source = self._read_source(abspath) if ext in PYTHON_EXTENSIONS else None
            pr = self._create_file_and_entities(graph, rel, ext, source)
            if pr is not None and pr.parse_failed:
                parse_failures += 1
            parse_results[rel] = pr
            try:
                indexed_files[rel] = os.path.getmtime(abspath)
            except OSError:
                pass

        module_to_file, defined_entities = self._build_maps(graph)
        for rel, pr in parse_results.items():
            if pr is not None and not pr.parse_failed:
                self._add_relationship_edges(graph, rel, pr, module_to_file, defined_entities)

        last_index_time = time.time()
        persisted = self.store.save(graph, last_index_time, indexed_files, meta.partial)
        self._enrich(graph)

        return ReindexResult(
            mode="incremental",
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            file_count=len(indexed_files),
            partial=meta.partial,
            parse_failures=parse_failures,
            changed_files=len(changed),
            deleted_files=len(deleted),
            duration_seconds=time.monotonic() - start,
            persisted=persisted,
        )

    # -- parsing ------------------------------------------------------------

    def _parse_python_file(self, path: str, source: str) -> FileParseResult:
        """Parse ``source`` with ``ast``; extract entities, imports, and calls.

        On ``SyntaxError``/``ValueError`` (or any parse error) returns a result
        flagged ``parse_failed=True`` so the caller keeps a file-only node.
        """
        result = FileParseResult()
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            result.parse_failed = True
            return result
        except Exception:
            result.parse_failed = True
            return result

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.functions.append(
                    (node.name, node.lineno, getattr(node, "end_lineno", node.lineno))
                )
            elif isinstance(node, ast.ClassDef):
                result.classes.append(
                    (node.name, node.lineno, getattr(node, "end_lineno", node.lineno))
                )

        # Imports (module nodes + depends-on candidates).
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.import_modules.add(alias.name)
                    result.depends_candidates |= _module_prefixes(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    result.import_modules.add(node.module)
                    result.depends_candidates |= _module_prefixes(node.module)
                    for alias in node.names:
                        result.depends_candidates.add(f"{node.module}.{alias.name}")

        # Calls associated with their enclosing top-level entity (or file).
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                caller = node.name
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        callee = _callee_name(sub.func)
                        if callee:
                            result.calls.append((caller, callee))
            else:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        callee = _callee_name(sub.func)
                        if callee:
                            result.calls.append((None, callee))

        return result

    # -- derivation helpers -------------------------------------------------

    def _create_file_and_entities(
        self, graph: Knowledge_Graph, rel: str, ext: str, source: Optional[str]
    ) -> Optional[FileParseResult]:
        """Create the file node and (for parseable Python) entity nodes + defines.

        Returns the parse result for Python files (used in the relationship pass),
        or ``None`` for non-Python text files (file-only node).
        """
        file_id = node_id("file", rel)
        language = "python" if ext in PYTHON_EXTENSIONS else ext.lstrip(".") or "text"
        file_attrs = {"path": rel, "language": language}

        if ext not in PYTHON_EXTENSIONS:
            graph.add_node(Graph_Node(id=file_id, type="file", attrs=file_attrs))
            return None

        if source is None:
            # Unreadable Python file -> file-only node flagged with parse_error.
            file_attrs["parse_error"] = True
            graph.add_node(Graph_Node(id=file_id, type="file", attrs=file_attrs))
            return FileParseResult(parse_failed=True)

        pr = self._parse_python_file(rel, source)
        if pr.parse_failed:
            file_attrs["parse_error"] = True
            graph.add_node(Graph_Node(id=file_id, type="file", attrs=file_attrs))
            return pr

        graph.add_node(Graph_Node(id=file_id, type="file", attrs=file_attrs))

        for name, ls, le in pr.functions:
            fid = node_id("function", rel, name, ls)
            graph.add_node(Graph_Node(
                id=fid, type="function",
                attrs={"path": rel, "name": name, "line_start": ls, "line_end": le},
            ))
            graph.add_edge(file_id, fid, "defines")

        for name, ls, le in pr.classes:
            cid = node_id("class", rel, name, ls)
            graph.add_node(Graph_Node(
                id=cid, type="class",
                attrs={"path": rel, "name": name, "line_start": ls, "line_end": le},
            ))
            graph.add_edge(file_id, cid, "defines")

        return pr

    def _build_maps(
        self, graph: Knowledge_Graph
    ) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
        """Build (module_name -> file rel) and (entity name -> [node ids]) maps."""
        module_to_file: Dict[str, str] = {}
        defined_entities: Dict[str, List[str]] = {}
        for nid, n in graph.nodes.items():
            if n.type == "file":
                path = n.attrs.get("path", "")
                if path.endswith(".py"):
                    module_to_file[_module_name_for_rel(path)] = path
            elif n.type in ("function", "class"):
                name = n.attrs.get("name")
                if name:
                    defined_entities.setdefault(name, []).append(nid)
        return module_to_file, defined_entities

    def _add_relationship_edges(
        self,
        graph: Knowledge_Graph,
        rel: str,
        pr: FileParseResult,
        module_to_file: Dict[str, str],
        defined_entities: Dict[str, List[str]],
    ) -> None:
        """Add imports / depends-on / calls edges for a single parsed file."""
        file_id = node_id("file", rel)

        # imports edges (+ module nodes).
        for mod in pr.import_modules:
            mod_id = node_id("module", name=mod)
            if mod_id not in graph.nodes:
                graph.add_node(Graph_Node(id=mod_id, type="module", attrs={"name": mod}))
            graph.add_edge(file_id, mod_id, "imports")

        # depends-on edges: imported module resolves to another indexed file.
        for cand in pr.depends_candidates:
            target_rel = module_to_file.get(cand)
            if target_rel and target_rel != rel:
                target_id = node_id("file", target_rel)
                if target_id in graph.nodes:
                    graph.add_edge(file_id, target_id, "depends-on")

        # calls edges: callee name matches a known defined entity.
        # Local map of this file's entity name -> node id for caller resolution.
        local_entities: Dict[str, str] = {}
        for name, ls, _le in pr.functions:
            local_entities[name] = node_id("function", rel, name, ls)
        for name, ls, _le in pr.classes:
            local_entities[name] = node_id("class", rel, name, ls)

        for caller, callee in pr.calls:
            targets = defined_entities.get(callee)
            if not targets:
                continue
            caller_id = local_entities.get(caller, file_id) if caller else file_id
            for target_id in targets:
                graph.add_edge(caller_id, target_id, "calls")

    # -- low-level helpers --------------------------------------------------

    @staticmethod
    def _read_source(abspath: str) -> Optional[str]:
        """Read a file as UTF-8 text, returning None on any error."""
        try:
            return Path(abspath).read_text(encoding="utf-8")
        except Exception:
            return None

    @staticmethod
    def _enrich(graph: Knowledge_Graph) -> None:
        """Best-effort Cognee enrichment; opt-in and non-blocking.

        Enrichment can be slow (and may stall for ~18s when cognee is installed
        but offline), so it never blocks a reindex. It is skipped entirely unless
        the ``OMNI_GRAPH_ENRICH`` environment flag is truthy; when enabled it runs
        on a background daemon thread so ``full_reindex`` / ``incremental_reindex``
        return immediately. Always silent-fail: any error (including an import
        failure or an inability to spawn the thread) is swallowed.
        """
        flag = (os.environ.get("OMNI_GRAPH_ENRICH", "") or "").strip().lower()
        if flag not in ("1", "true", "yes", "on"):
            # Default: skip enrichment so the reindex stays fast and offline-friendly.
            return

        def _run() -> None:
            try:
                from .enrichment import enrich
                enrich(graph)
            except Exception:
                pass

        try:
            import threading
            threading.Thread(
                target=_run, name="omni-graph-enrich", daemon=True
            ).start()
        except Exception:
            # Never raise into the caller, even if the thread cannot be started.
            pass


def _module_prefixes(dotted: str) -> Set[str]:
    """Return progressively shorter dotted prefixes of ``dotted``.

    ``"a.b.c"`` -> {"a.b.c", "a.b", "a"}. Used to resolve depends-on targets.
    """
    parts = dotted.split(".")
    return {".".join(parts[: i + 1]) for i in range(len(parts))}


def _module_name_for_rel(rel: str) -> str:
    """Map a relative ``.py`` path to its dotted module name.

    ``"a/b/c.py"`` -> ``"a.b.c"``; ``"a/b/__init__.py"`` -> ``"a.b"``.
    """
    no_ext = rel[:-3] if rel.endswith(".py") else rel
    parts = no_ext.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)
