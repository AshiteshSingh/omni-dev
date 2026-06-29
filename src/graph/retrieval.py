"""retrieval.py - GraphRAG retrieval over the Knowledge_Graph.

Combines entity matching with bounded edge traversal: seed nodes are matched by
query terms against their name/path/summary/module attributes, ranked by overlap,
expanded by BFS over ``depends-on`` and ``calls`` edges up to a configured depth,
augmented with linked ``Decision_Node``s, and truncated to a result limit. The
returned subgraph is structurally valid (every edge connects two returned nodes)
and is annotated when the graph is stale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .model import EDGE_TYPES, Graph_Edge, Graph_Node, Knowledge_Graph
from .staleness import annotate, is_stale

# Edge types traversed during GraphRAG expansion.
TRAVERSAL_EDGE_TYPES: Set[str] = {"depends-on", "calls"}


@dataclass
class RetrievalResult:
    """The subgraph returned by a retrieval, plus staleness annotation."""

    nodes: List[Graph_Node] = field(default_factory=list)
    edges: List[Graph_Edge] = field(default_factory=list)
    stale: bool = False
    notice: Optional[str] = None


def _tokenize(text: str) -> Set[str]:
    """Lowercase word tokens of length >= 2 (also split snake/camel-ish names)."""
    if not text:
        return set()
    raw = re.findall(r"[A-Za-z0-9_]+", text.lower())
    tokens: Set[str] = set()
    for word in raw:
        if len(word) >= 2:
            tokens.add(word)
        # Split underscore-delimited identifiers into parts as well.
        for part in word.split("_"):
            if len(part) >= 2:
                tokens.add(part)
    return tokens


def _matchable_tokens(node: Graph_Node) -> Set[str]:
    """Tokens drawn from a node's matchable attributes (name/path/summary/module)."""
    tokens: Set[str] = set()
    attrs = node.attrs or {}
    for key in ("name", "path", "summary", "rationale"):
        value = attrs.get(key)
        if isinstance(value, str):
            tokens |= _tokenize(value)
    if node.type == "module":
        name = attrs.get("name")
        if isinstance(name, str):
            tokens |= _tokenize(name)
    return tokens


class GraphRAGRetriever:
    """Selects the most relevant subgraph for a query or relationship question."""

    def __init__(self, graph: Knowledge_Graph, config, project_root=None, meta=None):
        self.graph = graph
        self.config = config
        self.project_root = project_root
        self.meta = meta

    # -- staleness ----------------------------------------------------------

    def _compute_stale(self) -> bool:
        """Best-effort staleness check; needs both project_root and meta."""
        if self.project_root is None or self.meta is None:
            return False
        try:
            from .builder import DEFAULT_EXCLUDED
            return is_stale(self.project_root, self.meta, DEFAULT_EXCLUDED)
        except Exception:
            return False

    def _finalize(
        self, nodes: List[Graph_Node], note: Optional[str] = None
    ) -> RetrievalResult:
        """Assemble a structurally valid result from a node list and annotate."""
        node_ids = {n.id for n in nodes}
        edges = [
            e for e in self.graph.edges
            if e.src in node_ids and e.dst in node_ids and e.type in EDGE_TYPES
        ]
        result = RetrievalResult(nodes=nodes, edges=edges, notice=note)
        return annotate(result, self._compute_stale())

    # -- general retrieval --------------------------------------------------

    def retrieve(self, query: str) -> RetrievalResult:
        """Run a GraphRAG retrieval for a free-text query.

        Steps: tokenize -> match & rank seeds -> BFS over depends-on/calls within
        the depth bound -> include linked Decision_Nodes -> truncate to the result
        limit -> annotate staleness. Returns an empty subgraph when nothing matches.
        """
        terms = _tokenize(query)
        if not terms:
            return self._finalize([])

        # 1) Seed matching + scoring.
        scored: List[tuple] = []  # (score, insertion_index, node)
        for idx, node in enumerate(self.graph.nodes.values()):
            overlap = terms & _matchable_tokens(node)
            if overlap:
                score = len(overlap)
                # Bonus when an entity's name matches directly.
                name = (node.attrs or {}).get("name")
                if isinstance(name, str) and (_tokenize(name) & terms):
                    score += 2
                scored.append((score, idx, node))

        if not scored:
            return self._finalize([])

        scored.sort(key=lambda t: (-t[0], t[1]))
        seeds = [node for _s, _i, node in scored]

        # 2) BFS expansion over traversal edges within the depth bound.
        ordered: List[Graph_Node] = []
        included: Set[str] = set()

        def _include(node: Graph_Node) -> None:
            if node.id not in included:
                included.add(node.id)
                ordered.append(node)

        for seed in seeds:
            _include(seed)

        max_depth = max(0, int(getattr(self.config, "max_depth", 0)))
        frontier = list(seeds)
        depth = 0
        while frontier and depth < max_depth:
            next_frontier: List[Graph_Node] = []
            for node in frontier:
                for nbr in self.graph.neighbors(
                    node.id, edge_types=TRAVERSAL_EDGE_TYPES, direction="out"
                ):
                    if nbr.id not in included:
                        _include(nbr)
                        next_frontier.append(nbr)
            frontier = next_frontier
            depth += 1

        # 3) Include Decision_Nodes linked to any included entity via relates-to.
        for nid in list(included):
            for dec in self.graph.neighbors(
                nid, edge_types={"relates-to"}, direction="in"
            ):
                if dec.type == "decision":
                    _include(dec)

        # 4) Truncate to the result limit (highest-ranked first; seeds lead).
        limit = max(0, int(getattr(self.config, "result_limit", 0)))
        final_nodes = ordered[:limit] if limit else ordered

        return self._finalize(final_nodes)

    # -- targeted relationship queries -------------------------------------

    def dependents_of(self, entity_name: str) -> RetrievalResult:
        """Return nodes connected to the named entity by INBOUND depends-on edges."""
        if not entity_name:
            return self._finalize([])

        target_file_ids = self._file_ids_for_name(entity_name)
        if not target_file_ids:
            return self._finalize([])

        nodes: List[Graph_Node] = []
        seen: Set[str] = set()
        for fid in target_file_ids:
            if fid in self.graph.nodes and fid not in seen:
                seen.add(fid)
                nodes.append(self.graph.nodes[fid])
            for dep in self.graph.neighbors(
                fid, edge_types={"depends-on"}, direction="in"
            ):
                if dep.id not in seen:
                    seen.add(dep.id)
                    nodes.append(dep)

        return self._finalize(nodes)

    def definition_of(self, symbol: str) -> RetrievalResult:
        """Return the file node + line span where ``symbol`` is defined."""
        if not symbol:
            return self._finalize([])

        nodes: List[Graph_Node] = []
        seen: Set[str] = set()
        for node in self.graph.nodes.values():
            if node.type in ("function", "class") and node.attrs.get("name") == symbol:
                if node.id not in seen:
                    seen.add(node.id)
                    nodes.append(node)
                path = node.attrs.get("path")
                if path:
                    from .model import node_id as _nid
                    file_id = _nid("file", path)
                    if file_id in self.graph.nodes and file_id not in seen:
                        seen.add(file_id)
                        nodes.append(self.graph.nodes[file_id])

        return self._finalize(nodes)

    def rationale_for(self, entity_name: str) -> RetrievalResult:
        """Return Decision_Nodes linked to the named entity (rationale lookup)."""
        if not entity_name:
            return self._finalize([])

        entity_ids = self._entity_ids_for_name(entity_name)
        nodes: List[Graph_Node] = []
        seen: Set[str] = set()
        for eid in entity_ids:
            if eid in self.graph.nodes and eid not in seen:
                seen.add(eid)
                nodes.append(self.graph.nodes[eid])
            for dec in self.graph.neighbors(
                eid, edge_types={"relates-to"}, direction="in"
            ):
                if dec.type == "decision" and dec.id not in seen:
                    seen.add(dec.id)
                    nodes.append(dec)

        return self._finalize(nodes)

    # -- helpers ------------------------------------------------------------

    def _entity_ids_for_name(self, name: str) -> List[str]:
        """Node ids of file/function/class entities matching ``name`` or path."""
        ids: List[str] = []
        for nid, node in self.graph.nodes.items():
            attrs = node.attrs or {}
            if node.type in ("function", "class") and attrs.get("name") == name:
                ids.append(nid)
            elif node.type == "file" and (
                attrs.get("path") == name or attrs.get("path", "").endswith("/" + name)
                or attrs.get("path") == name + ".py"
            ):
                ids.append(nid)
        return ids

    def _file_ids_for_name(self, name: str) -> List[str]:
        """File node ids associated with ``name`` (direct file or owning file)."""
        from .model import node_id as _nid

        file_ids: Set[str] = set()
        for node in self.graph.nodes.values():
            attrs = node.attrs or {}
            if node.type == "file":
                path = attrs.get("path", "")
                if path == name or path.endswith("/" + name) or path == name + ".py":
                    file_ids.add(node.id)
            elif node.type in ("function", "class") and attrs.get("name") == name:
                path = attrs.get("path")
                if path:
                    file_ids.add(_nid("file", path))
        return [fid for fid in file_ids if fid in self.graph.nodes]
