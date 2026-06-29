"""query_graph_tool.py - Agent tool for querying the Codebase Knowledge Graph.

``QueryGraphTool`` is a read-only ``BaseTool`` that lets the model ask the
persistent Knowledge_Graph relationship questions mid-task through the normal
tool path. It loads the locally persisted graph (never the network), detects the
kind of question being asked (general retrieval, dependents-of, definition-of, or
decision-rationale), runs the matching GraphRAG retrieval, and formats a concise,
structured string describing the matched Graph_Nodes and their relationships.

Resilience: loading a missing/corrupt store yields an empty graph (handled by
``GraphStore``), in which case the tool tells the caller to run ``/graph build``.
A staleness notice is prepended whenever the graph no longer reflects the tree.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base_tool import BaseTool


# Words that introduce a "what depends on X" style question.
_DEPENDENTS_PATTERNS = [
    re.compile(r"depends?\s+on\s+(.+)$", re.IGNORECASE),
    re.compile(r"dependents?\s+of\s+(.+)$", re.IGNORECASE),
    re.compile(r"who\s+(?:uses|imports)\s+(.+)$", re.IGNORECASE),
]

# Words that introduce a "where is X defined" style question.
_DEFINITION_PATTERNS = [
    re.compile(r"where\s+is\s+(.+?)\s+defined", re.IGNORECASE),
    re.compile(r"definition\s+of\s+(.+)$", re.IGNORECASE),
    re.compile(r"where\s+(?:is|are)\s+(.+?)\s+(?:declared|located)", re.IGNORECASE),
]

# Words that introduce a "why did we ..." rationale question.
_RATIONALE_PATTERNS = [
    re.compile(r"rationale\s+(?:for|behind)\s+(.+)$", re.IGNORECASE),
    re.compile(r"why\s+(?:did|do|does|is|are|was|were)\b(.+)$", re.IGNORECASE),
    re.compile(r"decision\s+(?:for|about|behind)\s+(.+)$", re.IGNORECASE),
]


def _extract_symbol(text: str) -> str:
    """Pull the most likely entity name out of a captured question fragment.

    Picks the last identifier-or-path-looking token (letters, digits, ``_``,
    ``.``, ``/``), trimming trailing punctuation/filler. Falls back to the whole
    stripped fragment when nothing matches.
    """
    fragment = (text or "").strip().strip("?.!,'\"")
    if not fragment:
        return ""
    tokens = re.findall(r"[A-Za-z0-9_./]+", fragment)
    if not tokens:
        return fragment

    # Prefer code-like tokens: a path/dotted name, snake_case, or CamelCase
    # identifier is far more likely to be the entity than a plain English word.
    def _is_code_like(tok: str) -> bool:
        if any(c in tok for c in "./_"):
            return True
        # CamelCase (an internal uppercase) or all-caps acronym of length >= 2.
        if re.search(r"[A-Z]", tok[1:]) or (tok.isupper() and len(tok) >= 2):
            return True
        return False

    code_like = [t for t in tokens if _is_code_like(t)]
    if code_like:
        return code_like[-1]

    # Drop trivial filler so e.g. "the graph module" -> "graph".
    filler = {
        "the", "a", "an", "module", "file", "class", "function", "do", "we",
        "was", "were", "is", "are", "did", "does", "chosen", "used", "added",
        "created", "made", "to", "for", "of", "on", "in", "this", "that",
    }
    meaningful = [t for t in tokens if t.lower() not in filler]
    if meaningful:
        return meaningful[-1]
    return tokens[-1]


class QueryGraphTool(BaseTool):
    """Read-only tool exposing GraphRAG queries over the Knowledge_Graph."""

    @property
    def name(self) -> str:
        return "query_graph"

    @property
    def description(self) -> str:
        return (
            "Query the codebase knowledge graph for code entities and their "
            "relationships. Read-only. Answers questions about imports, calls, "
            "dependencies (what depends on X / dependents of X), where a symbol "
            "is defined (definition of X), and the recorded rationale behind a "
            "decision (why was X chosen). Pass a natural-language question or a "
            "relationship query as 'query'. Consult this before acting to "
            "understand how code is connected."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language or relationship question about the "
                    "codebase, e.g. 'what depends on store.py', 'where is "
                    "GraphStore defined', 'why was JSON chosen', or 'retrieval "
                    "ranking'."
                ),
            },
        }

    @property
    def required_params(self) -> List[str]:
        return ["query"]

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, query: str = "", **kwargs) -> str:
        """Validate, load the graph, detect intent, retrieve, and format."""
        if not query or not query.strip():
            return "Error: a query is required"
        query = query.strip()

        # Load the locally persisted graph (offline, never raises).
        from src.graph.store import GraphStore
        from src.graph.config import get_graph_config
        from src.graph.retrieval import GraphRAGRetriever

        store = GraphStore()
        graph, meta = store.load()

        if not graph.nodes:
            return (
                "The knowledge graph is empty. Run `/graph build` to index the "
                "repository (a Reindex) before querying."
            )

        # project_root is the parent of the .cognee_data directory holding the store.
        try:
            project_root = str(store.path().parent.parent)
        except Exception:
            project_root = None

        config = get_graph_config(project_root)
        retriever = GraphRAGRetriever(graph, config, project_root=project_root, meta=meta)

        intent, symbol = self._detect_intent(query)

        if intent == "dependents":
            result = retriever.dependents_of(symbol)
            header = f"Dependents of '{symbol}'"
        elif intent == "definition":
            result = retriever.definition_of(symbol)
            header = f"Definition of '{symbol}'"
        elif intent == "rationale":
            result = retriever.rationale_for(symbol)
            header = f"Decision rationale related to '{symbol}'"
        else:
            result = retriever.retrieve(query)
            header = f"Graph results for: {query}"

        return self._format_result(header, result)

    # -- intent detection ---------------------------------------------------

    def _detect_intent(self, query: str) -> tuple:
        """Return ``(intent, symbol)`` for the query.

        ``intent`` is one of ``"dependents"``, ``"definition"``, ``"rationale"``,
        or ``"general"``. ``symbol`` is the extracted entity name (empty for the
        general case).
        """
        for pat in _DEFINITION_PATTERNS:
            m = pat.search(query)
            if m:
                return "definition", _extract_symbol(m.group(1))
        for pat in _DEPENDENTS_PATTERNS:
            m = pat.search(query)
            if m:
                return "dependents", _extract_symbol(m.group(1))
        for pat in _RATIONALE_PATTERNS:
            m = pat.search(query)
            if m:
                return "rationale", _extract_symbol(m.group(1))
        return "general", ""

    # -- formatting ---------------------------------------------------------

    def _format_result(self, header: str, result) -> str:
        """Render a RetrievalResult as a concise structured string."""
        lines: List[str] = []

        # Prepend the staleness notice when present (covers stale + empty notices).
        notice = getattr(result, "notice", None)
        if notice:
            lines.append(str(notice))
            lines.append("")

        lines.append(header)

        nodes = list(getattr(result, "nodes", []) or [])
        edges = list(getattr(result, "edges", []) or [])

        if not nodes:
            lines.append("No matching nodes found in the knowledge graph.")
            return "\n".join(lines)

        # Index edges by node id for quick relationship lookup.
        out_edges: Dict[str, List] = {}
        in_edges: Dict[str, List] = {}
        for e in edges:
            out_edges.setdefault(e.src, []).append(e)
            in_edges.setdefault(e.dst, []).append(e)

        id_to_label = {n.id: self._node_label(n) for n in nodes}

        lines.append(f"Matched {len(nodes)} node(s):")
        for node in nodes:
            lines.append(f"- {self._node_detail(node)}")
            rels = self._relationship_lines(node, out_edges, in_edges, id_to_label)
            for rel in rels:
                lines.append(f"    {rel}")

        return "\n".join(lines)

    @staticmethod
    def _node_label(node) -> str:
        """Short label for a node used when describing relationships."""
        attrs = node.attrs or {}
        name = attrs.get("name") or attrs.get("path") or node.id
        return f"{name}"

    @staticmethod
    def _node_detail(node) -> str:
        """One-line detail: type, name, path, and line span when available."""
        attrs = node.attrs or {}
        parts = [f"[{node.type}]"]
        name = attrs.get("name")
        if name:
            parts.append(str(name))
        path = attrs.get("path")
        if path:
            parts.append(f"({path})")
        line_start = attrs.get("line_start")
        line_end = attrs.get("line_end")
        if line_start is not None and line_end is not None:
            parts.append(f"L{line_start}-L{line_end}")
        elif line_start is not None:
            parts.append(f"L{line_start}")
        # Decision nodes carry their rationale text instead of a path/line.
        if node.type == "decision":
            rationale = attrs.get("rationale")
            if rationale:
                parts.append(f'- rationale: "{rationale}"')
        if node.type == "session":
            summary = attrs.get("summary")
            if summary:
                parts.append(f'- summary: "{summary}"')
        return " ".join(parts)

    @staticmethod
    def _relationship_lines(node, out_edges, in_edges, id_to_label) -> List[str]:
        """Describe a node's edges within the returned subgraph."""
        rels: List[str] = []
        for e in out_edges.get(node.id, []):
            target = id_to_label.get(e.dst, e.dst)
            rels.append(f"{e.type} -> {target}")
        for e in in_edges.get(node.id, []):
            source = id_to_label.get(e.src, e.src)
            rels.append(f"{source} -> {e.type} -> this")
        return rels
