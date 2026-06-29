"""src/graph - Codebase Knowledge Graph package for Omni-Dev.

A persistent, relationship-aware index of the repository that the agent consults
before acting. The package follows the resilience pattern proven by
``src/simple_memory.py``: a dependency-free local store is the always-works
primary, and optional Cognee enrichment is a best-effort secondary that fails
silently and never blocks.

Public surface:
- model:      Graph_Node, Graph_Edge, Knowledge_Graph, node_id, EDGE_TYPES, NODE_TYPES
- store:      GraphStore, GraphMeta
- config:     GraphConfig, get_graph_config
- builder:    Reindexer, ReindexResult, DEFAULT_EXCLUDED
- staleness:  changed_files, is_stale, annotate
- retrieval:  GraphRAGRetriever, RetrievalResult
- provenance: record_decision, record_session
- enrichment: enrich
"""

from .model import (
    EDGE_TYPES,
    NODE_TYPES,
    Graph_Node,
    Graph_Edge,
    Knowledge_Graph,
    node_id,
)
from .store import GraphStore, GraphMeta
from .config import GraphConfig, get_graph_config
from .builder import Reindexer, ReindexResult, DEFAULT_EXCLUDED
from .staleness import changed_files, is_stale, annotate, STALE_NOTICE
from .retrieval import GraphRAGRetriever, RetrievalResult
from .provenance import record_decision, record_session
from .enrichment import enrich

__all__ = [
    "EDGE_TYPES",
    "NODE_TYPES",
    "Graph_Node",
    "Graph_Edge",
    "Knowledge_Graph",
    "node_id",
    "GraphStore",
    "GraphMeta",
    "GraphConfig",
    "get_graph_config",
    "Reindexer",
    "ReindexResult",
    "DEFAULT_EXCLUDED",
    "changed_files",
    "is_stale",
    "annotate",
    "STALE_NOTICE",
    "GraphRAGRetriever",
    "RetrievalResult",
    "record_decision",
    "record_session",
    "enrich",
]
