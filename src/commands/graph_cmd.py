"""
graph_cmd.py - The `/graph` slash command (Knowledge Graph UI).

Surfaces the Codebase Knowledge Graph to the user with three subcommands:

- ``build``                -> run a full Reindex and report node/edge counts.
- ``query <text>``         -> GraphRAG retrieval, rendered as a themed Rich tree.
- ``neighborhood <name>``  -> a node plus its directly connected nodes/edges,
                              rendered as a themed Rich tree.

Rendering uses the shared visual system in ``src.cli.theme`` (semantic styles
such as ``app.accent``, ``app.muted``, ``tool.read``, ``status.warn``) so the
output matches the rest of the interface. Per Req 6.4/6.5 an empty graph prints
guidance to run ``build`` first and an unrecognized subcommand lists the
supported ones. Per Req 8.2 a staleness notice is shown alongside query /
neighborhood output when the graph is out of date.

The handler is defensive: it never raises into the REPL. Any unexpected failure
is caught and returned as a descriptive string so the session keeps running.
"""
from __future__ import annotations

from typing import List, Optional, Union

from rich.markup import escape
from rich.tree import Tree

from src.cli.theme import make_console
from src.graph.config import get_graph_config
from src.graph.store import GraphStore
from src.graph.builder import Reindexer, DEFAULT_EXCLUDED
from src.graph.retrieval import GraphRAGRetriever
from src.graph.staleness import STALE_NOTICE, is_stale
from src.graph.model import Graph_Node, Knowledge_Graph


_SUPPORTED = "Supported subcommands: build, query <text>, neighborhood <name>."
_EMPTY_GUIDANCE = (
    "The knowledge graph is empty. Run `/graph build` first to index this repository."
)


# ── argument handling ───────────────────────────────────────────────────────
def _normalize_args(args: Union[str, List[str], None]) -> str:
    """Coerce the raw arg input (string or list) into a single trimmed string."""
    if args is None:
        return ""
    if isinstance(args, (list, tuple)):
        return " ".join(str(a) for a in args).strip()
    return str(args).strip()


def _split_subcommand(text: str):
    """Return ``(subcommand_lower, rest)`` parsed from the arg string."""
    if not text:
        return "", ""
    pieces = text.split(None, 1)
    sub = pieces[0].lower()
    rest = pieces[1].strip() if len(pieces) > 1 else ""
    return sub, rest


# ── node/edge presentation helpers ───────────────────────────────────────────
def _node_label(node: Graph_Node) -> str:
    """Short, human-friendly label for a node (used in edge lines)."""
    attrs = node.attrs or {}
    for key in ("name", "path", "summary", "rationale"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    return node.id


def _node_detail(node: Graph_Node) -> str:
    """A richer one-line description of a node for the Nodes listing."""
    attrs = node.attrs or {}
    if node.type == "file":
        return attrs.get("path", node.id)
    if node.type in ("function", "class"):
        name = attrs.get("name", "?")
        path = attrs.get("path", "")
        ls = attrs.get("line_start")
        le = attrs.get("line_end")
        span = ""
        if ls is not None:
            span = f":L{ls}" + (f"-{le}" if le is not None else "")
        return f"{name}  ({path}{span})" if path else f"{name}{span}"
    if node.type == "module":
        return attrs.get("name", node.id)
    if node.type == "decision":
        return attrs.get("rationale", node.id)
    if node.type == "session":
        return attrs.get("summary", node.id)
    return _node_label(node)


# ── rendering ─────────────────────────────────────────────────────────────--
def _render_subgraph(console, title: str, nodes, edges, notice: Optional[str]) -> None:
    """Render a retrieval subgraph as a themed Rich tree (nodes grouped, edges shown)."""
    tree = Tree(f"[app.accent]{escape(title)}[/app.accent]")

    if notice:
        tree.add(f"[status.warn]{escape(notice)}[/status.warn]")

    if not nodes:
        tree.add("[app.muted]no matching nodes[/app.muted]")
        console.print(tree)
        return

    by_type: dict = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    nodes_branch = tree.add(f"[tool.read]Nodes ({len(nodes)})[/tool.read]")
    for ntype in sorted(by_type):
        group = nodes_branch.add(
            f"[app.accent]{escape(ntype)}[/app.accent] [app.muted]({len(by_type[ntype])})[/app.muted]"
        )
        for n in by_type[ntype]:
            group.add(f"[default]{escape(_node_detail(n))}[/default]")

    if edges:
        labels = {n.id: _node_label(n) for n in nodes}
        edges_branch = tree.add(f"[tool.edit]Relationships ({len(edges)})[/tool.edit]")
        for e in edges:
            src = labels.get(e.src, e.src)
            dst = labels.get(e.dst, e.dst)
            edges_branch.add(
                f"[app.muted]{escape(str(src))}[/app.muted] "
                f"[tool.run]{escape(e.type)}[/tool.run] "
                f"[app.muted]{escape(str(dst))}[/app.muted]"
            )

    console.print(tree)


def _render_neighborhood(console, graph: Knowledge_Graph, target: Graph_Node,
                         notice: Optional[str]) -> None:
    """Render a node plus its directly connected nodes/edges as a themed tree."""
    tree = Tree(
        f"[app.accent]{escape(target.type)}: {escape(_node_detail(target))}[/app.accent]"
    )

    if notice:
        tree.add(f"[status.warn]{escape(notice)}[/status.warn]")

    out_edges = [e for e in graph.edges if e.src == target.id]
    in_edges = [e for e in graph.edges if e.dst == target.id]

    if not out_edges and not in_edges:
        tree.add("[app.muted]no direct connections[/app.muted]")
        console.print(tree)
        return

    if out_edges:
        branch = tree.add(f"[tool.edit]outgoing ({len(out_edges)})[/tool.edit]")
        for e in out_edges:
            other = graph.nodes.get(e.dst)
            label = _node_label(other) if other is not None else e.dst
            branch.add(
                f"[tool.run]{escape(e.type)}[/tool.run] "
                f"[app.muted]\u2192 {escape(str(label))}[/app.muted]"
            )

    if in_edges:
        branch = tree.add(f"[tool.edit]incoming ({len(in_edges)})[/tool.edit]")
        for e in in_edges:
            other = graph.nodes.get(e.src)
            label = _node_label(other) if other is not None else e.src
            branch.add(
                f"[app.muted]{escape(str(label))} \u2192[/app.muted] "
                f"[tool.run]{escape(e.type)}[/tool.run]"
            )

    console.print(tree)


# ── lookup ───────────────────────────────────────────────────────────────--
def _find_node(graph: Knowledge_Graph, needle: str) -> Optional[Graph_Node]:
    """Find a node by exact id, name, or path (most specific match wins)."""
    # 1) exact id.
    node = graph.nodes.get(needle)
    if node is not None:
        return node

    # 2) name match (function/class/module).
    for n in graph.nodes.values():
        if (n.attrs or {}).get("name") == needle:
            return n

    # 3) path match (file), exact or suffix.
    for n in graph.nodes.values():
        path = (n.attrs or {}).get("path")
        if path and (path == needle or path.endswith("/" + needle) or path == needle + ".py"):
            return n

    return None


def _project_root(store: GraphStore) -> str:
    """The repository root that owns the resolved ``.cognee_data`` store."""
    # store.path() -> <root>/.cognee_data/knowledge_graph.json
    return str(store.path().parent.parent)


def _stale_notice(project_root: str, meta) -> Optional[str]:
    """Best-effort staleness notice; returns None when fresh or undeterminable."""
    try:
        return STALE_NOTICE if is_stale(project_root, meta, DEFAULT_EXCLUDED) else None
    except Exception:
        return None


# ── command entry point ──────────────────────────────────────────────────--
async def graph_command(args: Union[str, List[str], None] = "", console=None) -> str:
    """Dispatch the `/graph` subcommands (Req 6.1-6.5, 8.2).

    Args:
        args: The raw argument string (or list) following ``/graph`` — e.g.
            ``"build"``, ``"query auth flow"``, ``"neighborhood GraphStore"``.
        console: An optional themed Rich console to render trees onto. When
            omitted a themed console is created.

    Returns:
        A concise plain-text summary (also suitable for the REPL to print). Rich
        trees for query / neighborhood are rendered to ``console`` directly.
        Never raises: any failure is returned as a descriptive string.
    """
    if console is None:
        console = make_console()

    try:
        sub, rest = _split_subcommand(_normalize_args(args))

        # No subcommand or an unrecognized one -> list supported subcommands.
        if sub not in ("build", "query", "neighborhood"):
            return _SUPPORTED

        store = GraphStore()
        project_root = _project_root(store)
        config = get_graph_config(project_root)

        # ── build ──────────────────────────────────────────────────────────
        if sub == "build":
            result = Reindexer(project_root, store, config).full_reindex()
            summary = (
                f"Knowledge graph built: {result.node_count} nodes, "
                f"{result.edge_count} edges."
            )
            if result.partial:
                summary += " (partial: Index_Budget reached)"
            if not result.persisted:
                summary += " (warning: graph could not be persisted)"
            console.print(f"[status.ok]\u2713 {escape(summary)}[/status.ok]")
            return summary

        # query / neighborhood both need a loaded graph.
        graph, meta = store.load()
        if not graph.nodes:
            console.print(f"[status.warn]{escape(_EMPTY_GUIDANCE)}[/status.warn]")
            return _EMPTY_GUIDANCE

        # ── query ──────────────────────────────────────────────────────────
        if sub == "query":
            if not rest:
                return "Usage: /graph query <text>"
            retriever = GraphRAGRetriever(
                graph, config, project_root=project_root, meta=meta
            )
            res = retriever.retrieve(rest)
            _render_subgraph(
                console, f'Graph query: "{rest}"', res.nodes, res.edges, res.notice
            )
            if not res.nodes:
                return f'No graph nodes matched "{rest}".'
            return f'{len(res.nodes)} node(s), {len(res.edges)} relationship(s) for "{rest}".'

        # ── neighborhood ─────────────────────────────────────────────────--
        # sub == "neighborhood"
        if not rest:
            return "Usage: /graph neighborhood <name>"
        target = _find_node(graph, rest)
        if target is None:
            return (
                f"No graph node found matching '{rest}'. "
                f"Try `/graph query {rest}` or run `/graph build`."
            )
        notice = _stale_notice(project_root, meta)
        _render_neighborhood(console, graph, target, notice)
        return f"Neighborhood of '{_node_label(target)}'."

    except Exception as e:  # never raise into the REPL
        return f"\u26a0\ufe0f  /graph failed: {e}"
