# Omni-Dev

> A model-agnostic CLI coding agent with a persistent, hybrid graph-vector memory that never forgets your project — across sessions, model switches, and even repo deletions.

Omni-Dev is a terminal coding assistant that runs on **any** LLM provider (Gemini, Claude, GPT, Groq, Mistral, local Ollama, and more via [LiteLLM](https://github.com/BerriAI/litellm)) and gives the agent a durable long-term memory powered by [Cognee](https://github.com/topoteretes/cognee). Memory is stored as a hybrid graph + vector knowledge store that lives with your project, so the agent carries context across infinite sessions instead of waking up with amnesia every time.

---

## Why Omni-Dev is different

- **Persistent memory, not a context file.** The agent `remember`s facts, decisions, and code into a Cognee knowledge graph and `recall`s them later — even after the source files are gone.
- **A queryable codebase knowledge graph.** Ask "what depends on X", "where is X defined", or "why was X chosen" and get answers from a structured graph of your repo, including recorded decision rationale.
- **Truly model-agnostic.** Switch the model at runtime with `/model`; the Cognee memory engine follows your chat model automatically.
- **Works offline, never loses data.** A built-in JSON fallback store mirrors every memory, so a cloud or network failure can never lose your context.

---

## Requirements

- **Python 3.10+**
- **Git**
- An API key for at least one LLM provider (or a local Ollama install)

---

## Install

### Windows (PowerShell) — one-liner

```powershell
irm https://raw.githubusercontent.com/AshiteshSingh/omni-dev/main/install.ps1 | iex
```

This clones the repo to `%LOCALAPPDATA%\omni-dev`, creates a virtualenv, installs dependencies, and puts an `omni` command on your PATH. Open a new terminal afterward, then run `omni` from any project folder.

### Manual install

```powershell
git clone https://github.com/AshiteshSingh/omni-dev
cd omni-dev
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
python omni_dev.py
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the provider you want to use:

```dotenv
# Pick the active model (LiteLLM format)
OMNI_MODEL=vertex_ai/gemini-1.5-pro

# Example: Vertex AI (Gemini) — also used by the Cognee memory backend
LLM_PROVIDER=google_vertex_ai
LLM_MODEL=gemini-1.5-pro
EMBEDDING_PROVIDER=google_vertex_ai
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

Other providers (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, Ollama, AWS Bedrock, …) are all documented in `.env.example`. You can switch providers/models at runtime with `/model` and set keys with `/api_key`.

> **Note:** Your chat model and the Cognee reasoning model are auto-linked — set the model in one place. Embedding models are intentionally pinned (changing them invalidates the existing vector store).

---

## Quick start

```text
$ omni
> recall what we worked on last session
> add pagination to the /users endpoint
> /graph build
> why was JSON chosen for the graph store?
> /cost
```

Just talk to it. The agent reads your code, edits files, runs commands, and automatically remembers what it builds. At the start of a session it loads relevant past context from memory.

---

## Memory features

Omni-Dev exposes Cognee's full memory lifecycle. The agent uses these automatically, and you can also drive them directly:

| Capability | What it does |
|---|---|
| **remember** | Stores a fact, preference, or context into the Cognee graph (mirrored to the offline store). |
| **recall** | Retrieves past context via semantic + graph search, with the offline store as fallback. |
| **improve / memify** | Consolidates and enriches memory (global context index + truth subspace) so recall sharpens over time. |
| **forget** | Surgically removes a dataset, the memory layer, or everything. |
| **query_graph** | Read-only GraphRAG over your codebase: dependencies, definitions, and decision rationale. |

### Verifying memory

```powershell
python verify_memory.py        # end-to-end Cognee lifecycle diagnostic (surfaces every error)
python proof_repo_memory.py    # proves durable memory: ingest a file, delete it, still recall its content
```

---

## Slash commands

Type `/help` inside the CLI for the full list. Highlights:

| Command | Description |
|---|---|
| `/model` | Switch the active LLM provider/model at runtime. |
| `/api_key` | Set or update the API key for a provider. |
| `/cognee` | Choose the embedding model for graph memory (cloud or local/offline). |
| `/graph [build]` | Build or query the codebase knowledge graph. |
| `/index` | Index files into memory. |
| `/forget` | Run the Cognee forget lifecycle (memory / all / a dataset). |
| `/memify`, `/improve`, `/consolidate` | Enrich and consolidate long-term memory. |
| `/memory` | Inspect the memory store status. |
| `/compact` | Summarize the conversation into memory and shrink the context window. |
| `/cost`, `/tokens`, `/status` | Show session token usage and cost. |
| `/plan` | Produce a plan only (`proceed` to implement it). |
| `/developer`, `/dev` | Toggle autonomous plan → implement → build mode. |
| `/autonomous` | Toggle autonomous execution. |
| `/review [target]` | Review changes (defaults to `HEAD`). |
| `/commit` | Create a git commit. |
| `/pr_comments [target]` | Summarize PR comments. |
| `/release_notes` | Generate release notes. |
| `/init` | Analyze the codebase and bootstrap project context. |
| `/doctor` | Run environment/configuration diagnostics. |
| `/resume` | Resume a previous session from the transcript. |
| `/history` | Show the agent message history. |
| `/ctx_viz` | Visualize the current context window. |
| `/config` | View or set configuration values. |
| `/bug` | File a bug note. |
| `/clear` | Reset the conversation (long-term memory is preserved). |
| `/terminal_setup` | Configure terminal integration. |
| `/pwd`, `/ls` | Working-directory helpers. |
| `/help`, `?` | List all commands. |

---

## How memory storage works

Cognee's databases (relational, graph, and LanceDB vector store) are pinned into your project's `.cognee_data/` directory, so memory is durable and travels with the repo instead of being wiped on reinstall. If Cognee or the cloud is unreachable, a dependency-free JSON store (`.cognee_data/simple_memory.json`) transparently mirrors and serves your memories.

---

## Project layout

```text
omni_dev.py            # entry point
src/
  agent/               # agent loop, subagents, tool parsing, validation
  cli/                 # interactive interface, rendering, onboarding, theming
  commands/            # slash-command implementations
  graph/               # codebase knowledge graph (builder, retrieval, store)
  tools/               # agent tools (file, shell, search, memory, web, browser)
  cognee_paths.py      # durable Cognee storage + LLM/embedding configuration
  simple_memory.py     # offline JSON fallback memory store
  cost_tracker.py      # token/cost accounting and thresholds
  model_router.py      # provider/model routing
verify_memory.py       # memory lifecycle diagnostic
proof_repo_memory.py   # durable cross-session memory proof
```

---

## Development

```powershell
venv\Scripts\python -m pytest        # run the offline test suite (pytest + Hypothesis)
```

---

## Acknowledgements

Built with [Cognee](https://github.com/topoteretes/cognee) for the memory layer and [LiteLLM](https://github.com/BerriAI/litellm) for universal model access.
