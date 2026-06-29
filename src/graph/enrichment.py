"""enrichment.py - Optional, best-effort Cognee enrichment (silent-fail).

Mirrors the resilience pattern in ``src/tools/memory_tools.py``: the cognee
library and any network operation are wrapped in a broad ``try/except`` that
swallows import failures and any runtime error. Core reindex behavior never
depends on this succeeding.
"""

from __future__ import annotations

from .model import Knowledge_Graph


def enrich(graph: Knowledge_Graph) -> bool:
    """Attempt to push graph content to Cognee. Best effort; never raises.

    Returns True only if an enrichment path completed without error; returns
    False when cognee is unavailable or any error occurs. This is a secondary
    step — the local Knowledge_Graph remains the source of truth regardless.
    """
    try:
        import cognee  # noqa: F401  (import inside try: cognee is optional)

        # Pin durable storage roots FIRST so enrichment writes land in the
        # project .cognee_data store (never site-packages).
        try:
            from src import cognee_paths
            cognee_paths.configure_cognee_storage()
        except Exception:
            pass

        try:
            # Summarize the graph as a compact text payload for Cognee to index.
            # We avoid any blocking/long-running call beyond a single add+cognify
            # attempt; all failures are swallowed.
            summary = _summarize_graph(graph)
            if not summary:
                return False

            import asyncio

            async def _push() -> None:
                await cognee.add(summary, dataset_name="codebase_graph")
                await cognee.cognify()

            try:
                asyncio.get_running_loop()
                # Already inside an event loop; skip to avoid re-entrancy issues.
                return False
            except RuntimeError:
                asyncio.run(_push())
                return True
        except Exception:
            return False
    except Exception:
        return False


def _summarize_graph(graph: Knowledge_Graph) -> str:
    """Produce a compact textual summary of the graph for enrichment."""
    try:
        node_count = len(graph.nodes)
        edge_count = len(graph.edges)
        files = [
            n.attrs.get("path", n.id)
            for n in graph.nodes.values()
            if n.type == "file"
        ]
        head = ", ".join(sorted(files)[:50])
        return (
            f"Codebase knowledge graph: {node_count} nodes, {edge_count} edges. "
            f"Indexed files: {head}"
        )
    except Exception:
        return ""
