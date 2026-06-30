# Omni-Dev — Agent Context

Omni-Dev is a model-agnostic CLI coding agent with a persistent, hybrid
graph + vector memory powered by [Cognee](https://github.com/topoteretes/cognee)
and universal model access via [LiteLLM](https://github.com/BerriAI/litellm).
The agent carries context across sessions, model switches, and even repo
deletions. Built for the Hangover AI hackathon.

## Tech stack

- **Language:** Python 3.10+ (developed/tested on 3.12)
- **LLM access:** LiteLLM (Gemini, Claude, GPT, Groq, Mistral, Ollama, Bedrock…)
- **Memory engine:** Cognee 1.2.2 (relational + graph + LanceDB vector stores)
- **Offline fallback:** JSON store at `.cognee_data/simple_memory.json`
- **CLI/UI:** rich + prompt_toolkit
- **Browser automation:** Playwright + BeautifulSoup
- **Tests:** pytest + Hypothesis (property-based)

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
verify_memory.py       # end-to-end Cognee memory lifecycle diagnostic
proof_repo_memory.py   # durable cross-session memory proof
```

## How memory works

- **Source of truth:** the Cognee knowledge graph under `.cognee_data/system/`.
- **Resilience layer:** every write is mirrored to `simple_memory.json`, so a
  cloud/network failure can never lose a memory.
- **Lifecycle verbs** (exposed as agent tools in `src/tools/memory_tools.py`):
  `remember`, `recall`, `forget`, `improve_memory`.
- **Storage is pinned** into the project's `.cognee_data/` dir by
  `src/cognee_paths.py` so memory is durable and travels with the repo.
- **Auto-link:** Cognee's chat model follows the agent's `OMNI_MODEL`. The
  embedding model is intentionally pinned (changing it invalidates the existing
  vector store).

## Environment

- **OS:** Windows — shell is PowerShell/cmd. Never use Unix shell syntax
  (`&&`, `&`, `sleep`, `nohup`). Use separate commands; for background servers
  use a short timeout so the process spawns without blocking.
- **Config:** copy `.env.example` to `.env` and set a provider. The LLM key must
  be present for the Cognee graph layer (`cognify`/`recall`) to work.

## Conventions

- NEVER guess file paths — list/glob to verify first.
- ALWAYS read a file before editing it.
- Match existing style and libraries; do not introduce new dependencies casually.
- After completing a significant task, store a concise summary via `remember`.
- Run the offline test suite before claiming a change is done:
  `venv\Scripts\python -m pytest`.

## Commands

```powershell
venv\Scripts\python omni_dev.py        # run the CLI
venv\Scripts\python -m pytest          # run the offline test suite
venv\Scripts\python verify_memory.py   # diagnose the Cognee memory lifecycle
venv\Scripts\python proof_repo_memory.py  # prove durable cross-session memory
```

## Gotchas

- **Single-writer graph DB.** The Cognee/Ladybug (Kuzu) graph store takes an
  exclusive file lock. Only one process may open it at a time. If you see
  `Could not set lock on file ... .lbug (Error: 33)`, another Omni-Dev / verify
  process is running (or a previous run exited abnormally and left a zombie
  worker). Close other instances and retry. The offline JSON store keeps
  working regardless, so memory is never lost.
- The first run after a crash may fail on graph checks due to the stale lock;
  a clean retry succeeds.
