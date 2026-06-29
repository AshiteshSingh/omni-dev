# Implementation Plan: Codebase Knowledge Graph

## Overview

This plan implements the Codebase Knowledge Graph feature for Omni-Dev incrementally,
foundation-first. Each task builds on the previous: the data model and persistence come first,
then configuration, the AST builder (full then incremental reindex), staleness detection, GraphRAG
retrieval, provenance, optional Cognee enrichment, the `query_graph` agent tool, prompt-time
context injection in the agent loop, and finally the `/graph` slash command. All new code lives
under `src/graph/`, plus `src/tools/query_graph_tool.py` and `src/commands/graph_cmd.py`, wired into
`src/tools/__init__.py`, `src/agent/core.py`, `src/commands/__init__.py`, and
`src/cli/interface.py`.

Testing is dual: each of the 25 design Correctness Properties is implemented by a single
property-based test (Hypothesis, offline, `@settings(max_examples>=100)`, tagged
`# Feature: codebase-knowledge-graph, Property N: ...`), complemented by example/integration tests
for rendering, tool validation, enrichment, and staleness display. All tests run offline against
the existing `tests/` scaffolding and a no-network fixture; the cognee library is never required in
the core path.

Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP.

## Tasks

- [ ] 1. Create graph package, data model, and test scaffolding
  - [ ] 1.1 Create `src/graph/` package and `src/graph/model.py`
    - Add `src/graph/__init__.py`
    - Implement `Graph_Node`, `Graph_Edge` (frozen dataclasses), and the `Knowledge_Graph`
      container (`nodes: dict[str, Graph_Node]`, `edges: set[Graph_Edge]`)
    - Implement `EDGE_TYPES` / `NODE_TYPES` closed sets, `node_id(...)` stable deterministic id
      derivation, and `add_node` / `add_edge` / `remove_nodes_for_file` / `neighbors` /
      `to_dict` / `from_dict`
    - _Requirements: 1.2, 1.3, 3.2, 7.1_
  - [ ]* 1.2 Extend test scaffolding for the graph feature
    - Add graph generators to `tests/strategies.py`: a repository strategy (small `.py` files with
      controllable imports/defs/calls, injectable `Excluded_Path` dirs, broken files, arbitrary
      file counts), a `Knowledge_Graph` strategy, a corrupt-bytes strategy, and a query strategy
    - Add a fixture-repo builder fixture and a no-network fixture (monkeypatch
      `socket.socket.connect` and HTTP client entry points to raise) to `tests/conftest.py`
    - _Requirements: 10.5_
  - [ ]* 1.3 Write unit tests for the data model
    - Test `node_id` determinism, `remove_nodes_for_file` (removes file node, sub-entities, and
      incident edges), `neighbors` direction/edge-type filtering, and edge de-duplication
    - _Requirements: 1.3_

- [ ] 2. Implement the local persistence store
  - [ ] 2.1 Implement `src/graph/store.py`
    - Implement `GraphStore` resolving `.cognee_data/knowledge_graph.json` via the same
      project-root walk as `simple_memory._get_memory_path()`
    - Implement atomic `save(graph, last_index_time)` (temp file + `os.replace`, returns
      `bool`, never raises), `load() -> (Knowledge_Graph, GraphMeta)` with missing→empty
      (`needs_reindex=True`) and corrupt/unknown-version→empty fallbacks, plus `exists()` /
      `path()` and the `GraphMeta` dataclass
    - _Requirements: 3.1, 3.3, 3.4, 8.3_
  - [ ]* 2.2 Write property test for persistence round-trip
    - **Property 11: Persistence round-trip preserves the graph**
    - **Validates: Requirements 3.2, 10.2**
  - [ ]* 2.3 Write property test for missing/corrupt store fallback
    - **Property 12: Missing or corrupt store loads as empty without error**
    - **Validates: Requirements 3.3, 3.4, 10.3**
  - [ ]* 2.4 Write unit tests for store resilience
    - Assert `save()` returns a failure status (does not raise) on a simulated I/O error and the
      session can continue; assert `exists()` and `needs_reindex` semantics
    - _Requirements: 9.4_

- [ ] 3. Implement graph configuration
  - [ ] 3.1 Implement `src/graph/config.py`
    - Read `graphMaxFiles` (5000), `graphMaxSeconds` (30.0), `graphMaxDepth` (2),
      `graphResultLimit` (25) from `config_store` (project, then global, then defaults),
      mirroring `cost_tracker`'s defensive reads; any missing/invalid value falls back to the
      default without raising
    - _Requirements: 1.9, 4.4, 9.2, 9.3_
  - [ ]* 3.2 Write unit tests for configuration defaults and overrides
    - Test default values, project/global precedence, and invalid-value fallback
    - _Requirements: 9.2, 9.3, 4.4_

- [ ] 4. Implement full reindex in the AST builder
  - [ ] 4.1 Implement `src/graph/builder.py` full reindex
    - Implement `Reindexer` with `DEFAULT_EXCLUDED`, `ReindexResult`, `_parse_python_file`
      (extract top-level functions/classes, imports, call expressions; on
      `SyntaxError`/`ValueError` return `parse_failed=True`), and `full_reindex`
    - Derive nodes/edges: one `file` node per indexable file; `function`/`class` nodes with
      `defines` edges; `imports` edges to `module` nodes; `depends-on` edges to resolved repo
      files; `calls` edges to known defined entities; enforce `Index_Budget`; persist via
      `GraphStore` (including empty/partial graphs)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 9.2_
  - [ ]* 4.2 Write property test for file-node creation
    - **Property 1: Build creates exactly one file node per indexable source file**
    - **Validates: Requirements 1.1, 10.1**
  - [ ]* 4.3 Write property test for defines edges
    - **Property 2: Every function and class has a defining file and a defines edge**
    - **Validates: Requirements 1.2, 1.3**
  - [ ]* 4.4 Write property test for imports/depends-on edges
    - **Property 3: Imports produce imports and depends-on edges**
    - **Validates: Requirements 1.4, 1.6**
  - [ ]* 4.5 Write property test for calls edges
    - **Property 4: Calls to known defined entities produce calls edges**
    - **Validates: Requirements 1.5**
  - [ ]* 4.6 Write property test for excluded-path exclusion
    - **Property 5: Excluded paths are fully excluded**
    - **Validates: Requirements 1.7**
  - [ ]* 4.7 Write property test for parse tolerance
    - **Property 6: Unparseable files are tolerated**
    - **Validates: Requirements 1.8**
  - [ ]* 4.8 Write property test for the Index_Budget and persistence
    - **Property 7: Indexing respects the Index_Budget and always persists**
    - **Validates: Requirements 1.9, 9.2**
  - [ ]* 4.9 Write fixture-repo build example test
    - Run `full_reindex` against the deterministic fixture repo; assert the expected
      Code_Entity nodes and `defines`/`imports`/`depends-on`/`calls` edges exist
    - _Requirements: 10.1_

- [ ] 5. Implement incremental reindex
  - [ ] 5.1 Add incremental reindex to `src/graph/builder.py`
    - Implement `incremental_reindex`: identify files with `mtime > last_index_time` plus deleted
      previously-indexed files; remove their derived nodes/edges; re-derive surviving changed
      files; update `last_index_time` and `meta.indexed_files`; persist; no-op on graph data when
      nothing changed
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
  - [ ]* 5.2 Write property test for changed-set detection
    - **Property 8: Incremental reindex detects exactly the changed file set**
    - **Validates: Requirements 2.1, 8.3**
  - [ ]* 5.3 Write property test for precise re-derivation and deletion
    - **Property 9: Incremental reindex precisely re-derives changed files and drops deleted files**
    - **Validates: Requirements 2.2, 2.3**
  - [ ]* 5.4 Write property test for the no-change no-op
    - **Property 10: Incremental reindex with no changes is a no-op on graph data**
    - **Validates: Requirements 2.5**

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement staleness detection
  - [ ] 7.1 Implement `src/graph/staleness.py` and clear staleness on incremental reindex
    - Implement mtime-vs-`last_index_time` detection, the changed-file set computation, and a
      staleness-annotation helper (attaches a "graph is stale" notice to a result)
    - Wire `incremental_reindex` to clear the staleness condition on success
    - _Requirements: 8.1, 8.3, 8.4_
  - [ ]* 7.2 Write property test for clearing staleness
    - **Property 24: Incremental reindex clears staleness**
    - **Validates: Requirements 8.4**
  - [ ]* 7.3 Write unit tests for changed-file detection and annotation helper
    - Test changed-file set against known mtimes/deletions and that the annotation helper adds the
      notice only when stale
    - _Requirements: 8.3_

- [ ] 8. Implement GraphRAG retrieval
  - [ ] 8.1 Implement `src/graph/retrieval.py`
    - Implement `RetrievalResult` and `GraphRAGRetriever.retrieve` (tokenize query, match seed
      nodes by name/path/summary terms, rank seeds, BFS expand over `depends-on`/`calls` up to
      `config.max_depth`, include `Decision_Node`s linked to matched entities, truncate to
      `result_limit`, annotate staleness)
    - Implement `dependents_of` (inbound `depends-on`) and `definition_of` (file node + line span);
      return an empty subgraph on no match
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.4, 8.1, 9.3_
  - [ ]* 8.2 Write property test for seed matching
    - **Property 14: Retrieval seeds match query terms**
    - **Validates: Requirements 4.1**
  - [ ]* 8.3 Write property test for bounded reachability
    - **Property 15: Retrieved nodes are reachable from a seed within the depth bound**
    - **Validates: Requirements 4.2, 9.3, 10.4**
  - [ ]* 8.4 Write property test for subgraph structural validity
    - **Property 16: Returned subgraph is structurally valid**
    - **Validates: Requirements 4.3**
  - [ ]* 8.5 Write property test for the result limit
    - **Property 17: Retrieval never exceeds the result limit**
    - **Validates: Requirements 4.4**
  - [ ]* 8.6 Write property test for decision inclusion in retrieval
    - **Property 22: Linked decisions are included in retrieval**
    - **Validates: Requirements 7.4**
  - [ ]* 8.7 Write property test for staleness annotation of results
    - **Property 23: Results are annotated when the graph is stale**
    - **Validates: Requirements 8.1**
  - [ ]* 8.8 Write retrieval example test against the fixture repo
    - Assert retrieval for a known query returns the expected related nodes reachable by
      `depends-on`/`calls`, and that a disjoint query returns an empty subgraph
    - _Requirements: 4.5, 10.4_

- [ ] 9. Implement decision and session provenance
  - [ ] 9.1 Implement `src/graph/provenance.py`
    - Implement `record_decision` (creates a `Decision_Node` with rationale + `created_at` and a
      `relates-to` edge to each affected Code_Entity) and `record_session` (creates a
      `Session_Node` with summary + `created_at`)
    - _Requirements: 7.1, 7.2, 7.3_
  - [ ]* 9.2 Write property test for provenance creation and linking
    - **Property 21: Provenance nodes are created and linked**
    - **Validates: Requirements 7.1, 7.2, 7.3**

- [ ] 10. Implement optional Cognee enrichment
  - [ ] 10.1 Implement `src/graph/enrichment.py` and invoke it after reindex
    - Implement a silent-fail Cognee enrichment hook inside a broad `try/except` (swallowing
      import failures and any error), mirroring `memory_tools`
    - Call enrichment as a secondary step after a successful persist in `full_reindex`
    - _Requirements: 3.5, 3.6_
  - [ ]* 10.2 Write property test for enrichment resilience
    - **Property 13: Enrichment failure never blocks reindex**
    - **Validates: Requirements 3.6**
  - [ ]* 10.3 Write enrichment-attempt example test
    - Mock the enrichment hook; assert it is invoked exactly once after a successful reindex
    - _Requirements: 3.5_

- [ ] 11. Implement the query_graph agent tool
  - [ ] 11.1 Implement `src/tools/query_graph_tool.py` and register it
    - Implement `QueryGraphTool(BaseTool)` with `name`, `description`, `parameters`,
      `required_params`, `is_read_only()=True`, `needs_permissions()=False`, and an async `call`
      that validates the query, loads the graph (empty→"run reindex"), detects intent
      (general / dependents / definition / rationale), runs the matching retrieval, formats a
      structured string, and prepends a staleness notice when stale
    - Import in `src/tools/__init__.py`, add to `__all__`, and append to `get_all_tools()`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 7.5, 8.1_
  - [ ]* 11.2 Write property test for BaseTool interface conformance
    - **Property 18: Query_Graph_Tool conforms to the BaseTool interface**
    - **Validates: Requirements 5.1**
  - [ ]* 11.3 Write property test for structured match descriptions
    - **Property 19: Tool query returns a structured description of matches**
    - **Validates: Requirements 5.2**
  - [ ]* 11.4 Write property test for dependents/definition/rationale answers
    - **Property 20: Tool answers dependents, definition, and rationale questions correctly**
    - **Validates: Requirements 5.3, 5.4, 7.5**
  - [ ]* 11.5 Write tool input-validation example tests
    - Assert empty/missing query returns the "query required" error and an empty graph returns the
      "graph empty, run reindex" message
    - _Requirements: 5.5, 5.6_

- [ ] 12. Integrate prompt-time GraphRAG context into the agent loop
  - [ ] 12.1 Add prompt-time retrieval to `src/agent/core.py`
    - In `execute_task`, after static context load and before the first model call, run a
      best-effort GraphRAG retrieval for the prompt and inject the rendered subgraph as a new
      `codebaseGraph` context key via `format_system_prompt_with_context`; any failure leaves the
      static-context behavior unchanged
    - _Requirements: 4.6, 9.4_
  - [ ]* 12.2 Write prompt-time injection and resilience example tests
    - Assert `codebaseGraph` is present when matches exist and absent when retrieval fails, and
      that an injected retrieval failure does not raise into the loop
    - _Requirements: 4.6, 9.4_

- [ ] 13. Implement the /graph slash command
  - [ ] 13.1 Implement `src/commands/graph_cmd.py` and register it
    - Implement `graph_command` dispatching `build` (full reindex; print node/edge counts),
      `query <text>` (GraphRAG retrieval rendered as a themed `rich.tree.Tree`), and
      `neighborhood <name>` (node + direct neighbors as a themed tree); show a staleness notice on
      query/neighborhood when stale; instruct to run `build` first on an empty graph; list
      supported subcommands on an unknown subcommand
    - Register `graph` in `src/commands/__init__.py` `COMMANDS` and add it to the help/completer
      listing in `src/cli/interface.py`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 8.2_
  - [ ]* 13.2 Write /graph command example tests
    - Assert `build` prints correct counts, `query`/`neighborhood` produce a `rich.tree.Tree` with
      expected labels using theme styles, and an unknown subcommand lists supported subcommands
    - _Requirements: 6.1, 6.2, 6.3, 6.5_
  - [ ]* 13.3 Write empty-graph guidance and staleness-display example tests
    - Assert the "run build first" message on an empty graph and that a stale graph's rendered
      output includes the staleness notice
    - _Requirements: 6.4, 8.2_

- [ ] 14. Final verification
  - [ ]* 14.1 Write the no-network property test across core operations
    - **Property 25: All core operations make no network requests**
    - **Validates: Requirements 9.1**
  - [ ] 14.2 Run the full offline suite and CLI import smoke check
    - Run `pytest -q` offline and confirm all graph tests pass; add/run a smoke test that imports
      the CLI entry modules (`src.tools`, `src.commands`, `src.cli.interface`, `src.agent.core`)
      to verify the new tool/command wiring imports cleanly
    - _Requirements: 9.1, 10.5_

- [ ] 15. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP.
- Each task references specific requirement clauses for traceability; property-test tasks reference
  their design Property number and the requirements they validate.
- Property tests use Hypothesis with `@settings(max_examples>=100)` and the tag
  `# Feature: codebase-knowledge-graph, Property N: ...`, and run entirely offline.
- Every one of Requirements 1-10 and all 25 Correctness Properties is covered by at least one task.
- Checkpoints (tasks 6 and 15) provide incremental validation points.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1"] },
    { "id": 2, "tasks": ["1.3", "2.2", "2.3", "2.4", "3.2", "4.1"] },
    { "id": 3, "tasks": ["4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "5.4", "7.1"] },
    { "id": 5, "tasks": ["7.2", "7.3", "8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "9.1"] },
    { "id": 7, "tasks": ["9.2", "10.1"] },
    { "id": 8, "tasks": ["10.2", "10.3", "11.1"] },
    { "id": 9, "tasks": ["11.2", "11.3", "11.4", "11.5", "12.1"] },
    { "id": 10, "tasks": ["12.2", "13.1"] },
    { "id": 11, "tasks": ["13.2", "13.3", "14.1"] },
    { "id": 12, "tasks": ["14.2"] }
  ]
}
```
