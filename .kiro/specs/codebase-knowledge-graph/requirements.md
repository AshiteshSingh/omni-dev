# Requirements Document

## Introduction

The Codebase Knowledge Graph is the flagship differentiating feature for Omni-Dev, a Python CLI coding agent (a port of a TypeScript anon-kode/Claude Code style tool built on litellm, cognee, and Rich/prompt_toolkit). Unlike competing CLI agents (Claude Code, OpenCode, Hermes, OpenClaw), Omni-Dev maintains a persistent, relationship-aware Knowledge Graph of the repository that the agent consults BEFORE acting.

The graph indexes code entities (files/modules, functions, classes) and their relationships (imports, defines, calls, depends-on), plus higher-level nodes capturing architectural decisions and past session summaries. A GraphRAG retrieval layer returns the most relevant subgraph for a natural-language query or the current task by following edges, not just matching keywords. The graph is exposed to the model as an agent tool and to the user as a `/graph` slash command.

This feature follows the resilience pattern established by `src/simple_memory.py`: a reliable, dependency-free local store that works with zero cloud dependencies is the ALWAYS-WORKS primary, while optional Cognee enrichment is a best-effort secondary that fails silently and never blocks. It complements the static snapshot built by `src/context.py` with a queryable, incremental, relationship-aware index. All core behavior is verifiable offline with pytest and requires no network.

## Glossary

- **Omni_Dev**: The Python CLI coding agent that hosts this feature.
- **Knowledge_Graph**: The persistent, relationship-aware index of the repository, composed of Graph_Nodes and Graph_Edges.
- **Graph_Node**: A vertex in the Knowledge_Graph representing a single entity. Each Graph_Node has a stable identifier, a node type, and attributes (e.g., file path, name, line span, summary).
- **Graph_Edge**: A directed relationship between two Graph_Nodes with an edge type drawn from the set {imports, defines, calls, depends-on, relates-to}.
- **Code_Entity**: A Graph_Node whose type is one of {file, module, function, class}, derived from source code in the Repository.
- **Decision_Node**: A Graph_Node whose type is `decision`, representing a captured architectural decision with provenance (rationale, timestamp, and links to affected Code_Entities).
- **Session_Node**: A Graph_Node whose type is `session`, representing a summary of a past Omni_Dev working session, linked to the Code_Entities it touched.
- **Graph_Store**: The dependency-free local persistence layer (JSON or SQLite) for the Knowledge_Graph, located under the project's `.cognee_data` directory. It is the primary store and requires no network.
- **Cognee_Enrichment**: The optional, best-effort secondary integration with the cognee library that augments the Knowledge_Graph and fails silently on any error.
- **GraphRAG_Retrieval**: The retrieval operation that, given a query or task description, selects a relevant subgraph by combining entity matching with edge traversal (e.g., following depends-on and calls edges).
- **Query_Graph_Tool**: The agent tool (BaseTool subclass) that lets the model query the Knowledge_Graph mid-task and receive structured results through the normal tool path.
- **Graph_Command**: The user-facing `/graph` slash command for building/re-indexing, querying relationships, and rendering a readable summary in the terminal.
- **Reindex**: The operation that rebuilds Graph_Nodes and Graph_Edges. A full Reindex processes the whole Repository; an incremental Reindex processes only files that changed since the last index.
- **Staleness**: The condition in which the Knowledge_Graph no longer reflects the current working tree because tracked files changed after the last index time.
- **Repository**: The project directory tree rooted at the current working directory that Omni_Dev operates on.
- **Excluded_Path**: A directory or file path matching the noise-exclusion set {.git, node_modules, venv, .venv, __pycache__, dist, build, .next} or files that are not indexable source files.
- **Index_Budget**: The configured upper bounds on indexing work, expressed as a maximum file count and a maximum wall-clock duration, beyond which indexing stops gracefully with a partial graph.

## Requirements

### Requirement 1: Knowledge Graph Construction and Indexing

**User Story:** As an Omni-Dev user, I want the agent to scan my repository and build a knowledge graph of code entities and their relationships, so that the agent understands my codebase structure before acting.

#### Acceptance Criteria

1. WHEN a full Reindex is invoked, THE Omni_Dev SHALL scan the Repository and create one Code_Entity Graph_Node for each indexable source file.
2. WHEN a source file is parsed during Reindex, THE Omni_Dev SHALL create Code_Entity Graph_Nodes for each top-level function and class defined in that file.
3. WHEN a function or class is created as a Code_Entity, THE Omni_Dev SHALL create a `defines` Graph_Edge from the containing file Graph_Node to that Code_Entity.
4. WHEN an import statement is detected in a source file, THE Omni_Dev SHALL create an `imports` Graph_Edge from the importing file Graph_Node to the imported module Graph_Node.
5. WHERE a call to a known defined function or class is detected during parsing, THE Omni_Dev SHALL create a `calls` Graph_Edge from the calling Code_Entity to the called Code_Entity.
6. WHEN a module-level dependency between two files is established through an import, THE Omni_Dev SHALL create a `depends-on` Graph_Edge from the dependent file Graph_Node to the depended-upon file Graph_Node.
7. WHILE scanning the Repository, THE Omni_Dev SHALL skip every Excluded_Path and SHALL exclude its descendants from the Knowledge_Graph.
8. WHEN a source file cannot be parsed, THE Omni_Dev SHALL record the file as a Code_Entity Graph_Node without sub-entities and SHALL continue indexing the remaining files.
9. IF the number of scanned files reaches the Index_Budget file maximum or indexing reaches the Index_Budget duration maximum, THEN THE Omni_Dev SHALL stop scanning and SHALL persist the partial Knowledge_Graph, including when the partial Knowledge_Graph contains no Graph_Nodes.

### Requirement 2: Incremental Re-Indexing

**User Story:** As an Omni-Dev user, I want the graph to update only the parts that changed, so that re-indexing a large repository stays fast.

#### Acceptance Criteria

1. WHEN an incremental Reindex is invoked, THE Omni_Dev SHALL identify the set of source files whose modification time is later than the last index time.
2. WHEN a changed file is identified during incremental Reindex, THE Omni_Dev SHALL remove the existing Graph_Nodes and Graph_Edges derived from that file before re-deriving them.
3. WHEN a previously indexed source file no longer exists during incremental Reindex, THE Omni_Dev SHALL remove the Graph_Nodes and Graph_Edges derived from that file.
4. WHEN an incremental Reindex completes, THE Omni_Dev SHALL update the last index time to the Reindex completion time.
5. IF no source files changed since the last index time, THEN THE Omni_Dev SHALL complete the incremental Reindex without modifying existing Graph_Nodes and Graph_Edges.

### Requirement 3: Local Persistence and Resilience

**User Story:** As an Omni-Dev user, I want the knowledge graph stored locally with zero cloud dependencies, so that it always works even when external services are unavailable.

#### Acceptance Criteria

1. WHEN a Reindex completes, THE Graph_Store SHALL persist the Knowledge_Graph to the local project `.cognee_data` directory without requiring network access.
2. WHEN the Knowledge_Graph is loaded from the Graph_Store after a prior persist, THE Graph_Store SHALL return a Knowledge_Graph whose Graph_Nodes and Graph_Edges are equal to those persisted (round-trip property).
3. IF the Graph_Store file is missing when a load is requested, THEN THE Omni_Dev SHALL return an empty Knowledge_Graph and SHALL report that a Reindex is required.
4. IF the Graph_Store file is present but corrupt when a load is requested, THEN THE Omni_Dev SHALL discard the corrupt content and SHALL return an empty Knowledge_Graph rather than raising an error.
5. WHEN a Reindex completes, THE Omni_Dev SHALL attempt Cognee_Enrichment as a secondary step.
6. IF Cognee_Enrichment raises any error or the cognee library is unavailable, THEN THE Omni_Dev SHALL continue using the local Knowledge_Graph and SHALL complete the operation successfully.

### Requirement 4: GraphRAG Retrieval

**User Story:** As an Omni-Dev user, I want the agent to retrieve the most relevant related parts of the graph for a query or task, so that its answers reflect real code relationships rather than keyword matches alone.

#### Acceptance Criteria

1. WHEN a GraphRAG_Retrieval is requested with a query string, THE Omni_Dev SHALL select an initial set of Graph_Nodes whose attributes match the query terms.
2. WHEN an initial set of Graph_Nodes is selected, THE GraphRAG_Retrieval SHALL expand the result by traversing `depends-on` and `calls` Graph_Edges to a bounded edge depth.
3. WHEN a GraphRAG_Retrieval returns results, THE Omni_Dev SHALL return a subgraph containing the selected Graph_Nodes, the traversed Graph_Edges, and the relationship type for each included Graph_Edge.
4. WHEN a GraphRAG_Retrieval returns more candidate Graph_Nodes than the configured result limit, THE Omni_Dev SHALL return only the highest-ranked Graph_Nodes up to that limit.
5. IF no Graph_Node matches the query, THEN THE GraphRAG_Retrieval SHALL return an empty subgraph.
6. WHEN Omni_Dev begins processing a user prompt, THE Omni_Dev SHALL perform a GraphRAG_Retrieval for the prompt and SHALL make the resulting subgraph available as context for that prompt.

### Requirement 5: Query Graph Agent Tool

**User Story:** As the agent model, I want a tool to ask the knowledge graph questions mid-task, so that I can consult relationships before acting.

#### Acceptance Criteria

1. THE Query_Graph_Tool SHALL expose a `name`, a `description`, a `parameters` schema, a `required_params` list, an `is_read_only` value of true, a `needs_permissions` value of false, an async `call` method, and a `to_schema` method, consistent with the BaseTool interface.
2. WHEN the Query_Graph_Tool `call` method is invoked with a query argument, THE Query_Graph_Tool SHALL perform a GraphRAG_Retrieval and SHALL return a structured string result describing the matching Graph_Nodes and their relationships.
3. WHEN the Query_Graph_Tool is invoked with a relationship question about dependents of a named Code_Entity, THE Query_Graph_Tool SHALL return the Graph_Nodes connected to that Code_Entity by inbound `depends-on` Graph_Edges.
4. WHEN the Query_Graph_Tool is invoked with a definition-location question for a named symbol, THE Query_Graph_Tool SHALL return the file Graph_Node and line span where that symbol is defined.
5. IF the Query_Graph_Tool is invoked with a missing or empty query argument, THEN THE Query_Graph_Tool SHALL return an error result indicating that a query is required.
6. IF the Knowledge_Graph is empty when the Query_Graph_Tool is invoked, THEN THE Query_Graph_Tool SHALL return a result indicating that the graph is empty and a Reindex is required.

### Requirement 6: Graph Slash Command

**User Story:** As an Omni-Dev user, I want a `/graph` command to build, query, and visualize the knowledge graph in my terminal, so that I can inspect my codebase relationships interactively.

#### Acceptance Criteria

1. WHEN the user invokes the Graph_Command with a build subcommand, THE Omni_Dev SHALL run a Reindex and SHALL display the resulting Graph_Node count and Graph_Edge count.
2. WHEN the user invokes the Graph_Command with a query subcommand and a query string, THE Omni_Dev SHALL perform a GraphRAG_Retrieval and SHALL render the resulting subgraph as a Rich tree using the project theme.
3. WHEN the user invokes the Graph_Command with a neighborhood subcommand for a named Graph_Node, THE Omni_Dev SHALL render that Graph_Node and its directly connected Graph_Nodes and Graph_Edges as a Rich tree.
4. IF the user invokes the Graph_Command for a query or neighborhood subcommand while the Knowledge_Graph is empty, THEN THE Omni_Dev SHALL display a message instructing the user to run the build subcommand first.
5. IF the user invokes the Graph_Command with an unrecognized subcommand, THEN THE Omni_Dev SHALL display the list of supported subcommands.

### Requirement 7: Decision and Session Provenance

**User Story:** As an Omni-Dev user, I want architectural decisions and past session summaries linked into the graph, so that the agent can answer "why did we do X" from recorded provenance.

#### Acceptance Criteria

1. WHEN an architectural decision is recorded, THE Omni_Dev SHALL create a Decision_Node containing the decision rationale and a creation timestamp.
2. WHEN a Decision_Node references one or more affected Code_Entities, THE Omni_Dev SHALL create a `relates-to` Graph_Edge from the Decision_Node to each affected Code_Entity.
3. WHEN an Omni_Dev session summary is recorded, THE Omni_Dev SHALL create a Session_Node containing the summary text and a creation timestamp.
4. WHEN a GraphRAG_Retrieval matches a Code_Entity that is linked to a Decision_Node, THE GraphRAG_Retrieval SHALL include the linked Decision_Node in the returned subgraph.
5. WHEN the Query_Graph_Tool is invoked with a rationale question referencing a Code_Entity, THE Query_Graph_Tool SHALL return the rationale text of Decision_Nodes linked to that Code_Entity.

### Requirement 8: Staleness Detection and Consistency

**User Story:** As an Omni-Dev user, I want the agent to know when the graph is out of date, so that I am never given stale information presented as current.

#### Acceptance Criteria

1. WHEN a GraphRAG_Retrieval or Query_Graph_Tool result is produced WHILE Staleness is present, THE Omni_Dev SHALL annotate the result with a notice that the Knowledge_Graph is stale.
2. WHEN the Graph_Command query or neighborhood subcommand renders results WHILE Staleness is present, THE Omni_Dev SHALL display a Staleness notice alongside the rendered output.
3. WHEN Staleness is detected, THE Omni_Dev SHALL identify the set of changed source files relative to the last index time.
4. WHEN an incremental Reindex completes successfully, THE Omni_Dev SHALL clear the Staleness condition.

### Requirement 9: Performance and Safety Bounds

**User Story:** As an Omni-Dev user, I want indexing and retrieval to stay within safe bounds and require no network, so that the feature is fast and never blocks my work.

#### Acceptance Criteria

1. THE Omni_Dev SHALL complete all core Knowledge_Graph operations, including Reindex, persistence, GraphRAG_Retrieval, and Query_Graph_Tool calls, without making network requests.
2. WHILE a Reindex is running, THE Omni_Dev SHALL enforce the Index_Budget file maximum and the Index_Budget duration maximum.
3. WHEN a GraphRAG_Retrieval traverses Graph_Edges, THE Omni_Dev SHALL limit traversal to the configured bounded edge depth.
4. IF any Knowledge_Graph operation fails, THEN THE Omni_Dev SHALL report the failure and SHALL allow the current session to continue.

### Requirement 10: Offline Verifiability

**User Story:** As an Omni-Dev maintainer, I want automated offline tests for the knowledge graph, so that graph construction, persistence, and retrieval are verified without network access.

#### Acceptance Criteria

1. WHEN the graph build test runs against a fixture Repository, THE test SHALL assert that the expected Code_Entity Graph_Nodes and Graph_Edges are created.
2. WHEN the persistence round-trip test runs, THE test SHALL assert that a Knowledge_Graph persisted and then loaded from the Graph_Store is equal to the original.
3. WHEN the corrupt-store test runs against a corrupt Graph_Store file, THE test SHALL assert that loading the Graph_Store yields an empty Knowledge_Graph, treating a gracefully handled load failure as equivalent to an empty Knowledge_Graph.
4. WHEN the retrieval test runs against a fixture Repository, THE test SHALL assert that GraphRAG_Retrieval for a known query returns the expected related Graph_Nodes reachable by `depends-on` or `calls` Graph_Edges.
5. THE offline test suite SHALL execute using pytest without requiring network access or the cognee library.
