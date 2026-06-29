"""model.py - Core data model for the Codebase Knowledge Graph.

Defines the immutable ``Graph_Node`` and ``Graph_Edge`` value types, the mutable
``Knowledge_Graph`` container, the closed ``EDGE_TYPES`` / ``NODE_TYPES`` sets, and
the deterministic ``node_id`` derivation that makes re-deriving an unchanged entity
reproduce an identical id (the basis for round-trip and incremental correctness).

All ids are pure functions of (type, normalized relative path, name, line span).
Paths are normalized to forward-slash relative form so ids are stable across OSes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


# Closed sets drawn from the requirements glossary.
EDGE_TYPES: Set[str] = {"imports", "defines", "calls", "depends-on", "relates-to"}
NODE_TYPES: Set[str] = {"file", "module", "function", "class", "decision", "session"}


def _normalize_path(path: Optional[str]) -> str:
    """Normalize a path to forward-slash relative form.

    Converts OS separators to ``/`` and strips a leading ``./`` so equivalent
    paths produce identical ids regardless of platform.
    """
    if not path:
        return ""
    norm = str(path).replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


@dataclass(frozen=True)
class Graph_Node:
    """A vertex in the Knowledge_Graph.

    Attributes:
        id: Stable, deterministic identifier (see :func:`node_id`).
        type: One of :data:`NODE_TYPES`.
        attrs: Type-specific attributes (path, name, line span, summary, etc.).
    """

    id: str
    type: str
    attrs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Graph_Edge:
    """A directed, typed relationship between two Graph_Nodes."""

    src: str
    dst: str
    type: str


@dataclass
class Knowledge_Graph:
    """The persistent, relationship-aware index of the repository.

    ``nodes`` maps node id -> Graph_Node; ``edges`` is a de-duplicated set of
    directed edges. Two graphs compare equal when their nodes and edges are equal
    (order-independent), which underpins the persistence round-trip property.
    """

    nodes: Dict[str, Graph_Node] = field(default_factory=dict)
    edges: Set[Graph_Edge] = field(default_factory=set)

    # -- mutation -----------------------------------------------------------

    def add_node(self, node: Graph_Node) -> None:
        """Insert or replace a node keyed by its id."""
        self.nodes[node.id] = node

    def add_edge(self, src: str, dst: str, type: str) -> None:
        """Add a directed, typed edge (de-duplicated by (src, dst, type))."""
        self.edges.add(Graph_Edge(src=src, dst=dst, type=type))

    def remove_nodes_for_file(self, path: str) -> None:
        """Remove the file node, its sub-entity nodes, and all incident edges.

        Sub-entities are the ``function``/``class`` nodes whose ``attrs.path``
        equals the (normalized) file path. Shared ``module`` nodes are left in
        place; only edges incident to removed nodes are dropped.
        """
        rel = _normalize_path(path)
        to_remove: Set[str] = set()

        file_id = node_id("file", rel)
        if file_id in self.nodes:
            to_remove.add(file_id)

        for nid, node in self.nodes.items():
            if node.type in ("file", "function", "class") and node.attrs.get("path") == rel:
                to_remove.add(nid)

        for nid in to_remove:
            self.nodes.pop(nid, None)

        if to_remove:
            self.edges = {
                e for e in self.edges
                if e.src not in to_remove and e.dst not in to_remove
            }

    # -- traversal ----------------------------------------------------------

    def neighbors(
        self,
        node_id: str,
        edge_types: Optional[Set[str]] = None,
        direction: str = "out",
    ) -> List[Graph_Node]:
        """Return nodes adjacent to ``node_id``.

        Args:
            node_id: The node whose neighbors are requested.
            edge_types: If given, only traverse edges of these types.
            direction: ``"out"`` follows outbound edges (src == node_id);
                ``"in"`` follows inbound edges (dst == node_id).
        """
        result: List[Graph_Node] = []
        seen: Set[str] = set()
        for e in self.edges:
            if edge_types is not None and e.type not in edge_types:
                continue
            if direction == "out" and e.src == node_id:
                other = e.dst
            elif direction == "in" and e.dst == node_id:
                other = e.src
            else:
                continue
            if other not in seen and other in self.nodes:
                seen.add(other)
                result.append(self.nodes[other])
        return result

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize nodes and edges to the on-disk schema's node/edge arrays."""
        return {
            "nodes": [
                {"id": n.id, "type": n.type, "attrs": n.attrs}
                for n in self.nodes.values()
            ],
            "edges": [
                {"src": e.src, "dst": e.dst, "type": e.type}
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Knowledge_Graph":
        """Reconstruct a Knowledge_Graph from a (possibly full) on-disk document.

        Tolerant of the full document shape ``{version, meta, nodes, edges}`` as
        well as a bare ``{nodes, edges}`` payload. Malformed entries are skipped.
        """
        graph = cls()
        if not isinstance(data, dict):
            return graph
        for raw in data.get("nodes", []) or []:
            if not isinstance(raw, dict):
                continue
            nid = raw.get("id")
            ntype = raw.get("type")
            attrs = raw.get("attrs", {})
            if isinstance(nid, str) and isinstance(ntype, str):
                graph.nodes[nid] = Graph_Node(
                    id=nid, type=ntype, attrs=attrs if isinstance(attrs, dict) else {}
                )
        for raw in data.get("edges", []) or []:
            if not isinstance(raw, dict):
                continue
            src = raw.get("src")
            dst = raw.get("dst")
            etype = raw.get("type")
            if isinstance(src, str) and isinstance(dst, str) and isinstance(etype, str):
                graph.edges.add(Graph_Edge(src=src, dst=dst, type=etype))
        return graph


def node_id(
    node_type: str,
    path: Optional[str] = None,
    name: Optional[str] = None,
    line_start: Optional[int] = None,
) -> str:
    """Derive a stable, deterministic node id.

    Forms by node type:
        file:     ``file::<relpath>``
        module:   ``module::<modname>``           (modname from ``name`` or ``path``)
        function: ``function::<relpath>::<name>#L<line>``
        class:    ``class::<relpath>::<name>#L<line>``
        decision: ``decision::<sha1[:12]>``        (hash of ``path`` content basis)
        session:  ``session::<timestamp>``         (timestamp from ``name`` or ``path``)

    Re-deriving an unchanged entity reproduces an identical id, so an incremental
    reindex of an unchanged file is a no-op at the data level.
    """
    if node_type == "file":
        return f"file::{_normalize_path(path)}"

    if node_type == "module":
        mod = name if name is not None else (path or "")
        return f"module::{mod}"

    if node_type in ("function", "class"):
        rel = _normalize_path(path)
        line_part = f"#L{line_start}" if line_start is not None else ""
        return f"{node_type}::{rel}::{name}{line_part}"

    if node_type == "decision":
        basis = name if name is not None else (path or "")
        digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
        return f"decision::{digest}"

    if node_type == "session":
        stamp = name if name is not None else (path or "")
        return f"session::{stamp}"

    # Generic fallback for forward compatibility.
    return f"{node_type}::{_normalize_path(path)}"
