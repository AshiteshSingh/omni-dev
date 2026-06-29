"""provenance.py - Decision and Session provenance capture.

Creates ``Decision_Node``s (architectural decisions with rationale + timestamp)
and ``Session_Node``s (session summaries), each linked to the Code_Entities they
affect/touch via ``relates-to`` edges. Node ids are derived through
``model.node_id`` so they are stable and deterministic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from .model import Graph_Node, Knowledge_Graph, node_id


def _now_iso() -> str:
    """Return the current local time as an ISO-8601 string (with microseconds)."""
    return datetime.now().isoformat()


def record_decision(
    graph: Knowledge_Graph,
    rationale: str,
    affected_entity_ids: Optional[Iterable[str]] = None,
    created_at: Optional[str] = None,
) -> Graph_Node:
    """Create a Decision_Node and link it to each affected Code_Entity.

    The decision carries ``rationale`` and a creation timestamp. A ``relates-to``
    edge is added from the Decision_Node to each id in ``affected_entity_ids``.
    Returns the created node.
    """
    created_at = created_at or _now_iso()
    rationale = rationale or ""

    # Hash basis combines rationale + timestamp so distinct decisions get distinct ids.
    basis = f"{rationale}|{created_at}"
    nid = node_id("decision", name=basis)

    node = Graph_Node(
        id=nid,
        type="decision",
        attrs={"rationale": rationale, "created_at": created_at},
    )
    graph.add_node(node)

    for eid in (affected_entity_ids or []):
        if eid:
            graph.add_edge(nid, eid, "relates-to")

    return node


def record_session(
    graph: Knowledge_Graph,
    summary: str,
    touched_entity_ids: Optional[Iterable[str]] = None,
    created_at: Optional[str] = None,
) -> Graph_Node:
    """Create a Session_Node and link it to each touched Code_Entity.

    The session carries ``summary`` text and a creation timestamp. A ``relates-to``
    edge is added from the Session_Node to each id in ``touched_entity_ids``.
    Returns the created node.
    """
    created_at = created_at or _now_iso()
    summary = summary or ""

    nid = node_id("session", name=created_at)

    node = Graph_Node(
        id=nid,
        type="session",
        attrs={"summary": summary, "created_at": created_at},
    )
    graph.add_node(node)

    for eid in (touched_entity_ids or []):
        if eid:
            graph.add_edge(nid, eid, "relates-to")

    return node
