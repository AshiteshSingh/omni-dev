# Omni-Dev — Demo Video Script

Total runtime target: **3.5–4 minutes**.

Flow: **Hook (spoken) → Code tour (45s) → Live demo in 4 acts → Comparison closer.**

Open with the code so judges trust it's real, then spend most of the time on the
live demo because that's the emotional payoff.

---

## PRE-RECORDING CHECKLIST (do this BEFORE you hit record)

- [ ] Close every other `omni` / `verify_memory.py` / `proof_repo_memory.py` window.
      The graph DB is single-writer; a leftover process causes
      `Could not set lock on file ... Error: 33`.
- [ ] Activate the venv and confirm `omni` launches cleanly once, then exit.
- [ ] Pre-build the codebase graph so Act 4 is instant:  `> /graph build`
- [ ] Pre-warm the model you'll switch to in Act 3 (so there's no cold-start wait).
- [ ] Make sure `.env` has a working LLM key (so the graph layer works live).
- [ ] Increase terminal font size. Use a clean, dark theme.
- [ ] Have two files ready to show in the editor:
      `src/tools/memory_tools.py` and `src/cognee_paths.py`.
- [ ] Optional: clear the scratch so Act 1 is clean, but KEEP existing memory if you
      want to also show "it remembers older sessions."

---

## [0:00–0:20] HOOK  (talk over your desktop / logo)

> "Every AI coding assistant has the same problem — amnesia. Close the session,
> and it forgets your entire project. Switch models, and you start from zero.
> This is Omni-Dev: a coding agent that runs on *any* model and has a permanent,
> queryable memory of your work. Let me show you how it's built, then prove it."

---

## [0:20–1:05] CODE TOUR  (screen-share the editor — keep it tight)

Open `src/tools/memory_tools.py`:

> "The whole design is two memory layers running in parallel. Here in the
> `remember` tool — every fact is written to the Cognee knowledge graph, which is
> the smart source of truth, AND mirrored to a local JSON store. So a cloud
> outage can never lose a memory."

Scroll to the `recall` tool:

> "On read, it merges both — a fast exact search over the graph's stored text,
> plus an LLM-synthesized answer over the graph relationships, with the offline
> store as a guaranteed fallback."

Open `src/cognee_paths.py`:

> "And this is the durability trick: Omni-Dev pins Cognee's graph, vector, and
> relational databases into the project's own `.cognee_data` folder — not the
> Python install — so memory survives reinstalls and travels with the repo."

> "Three stores under the hood: a graph database, a LanceDB vector store, and
> SQLite. Now let me prove it actually works."

---

## [1:05–1:35] ACT 1 — Memory across sessions

[Switch to the terminal. Launch:]
```
omni
```
> "I'll teach it a few project facts."
```
> remember my project codename is ZORBLAX-9, the deploy key rotates every 19 days, and I prefer tabs over spaces
```
> "Now watch — I'm going to completely close the program. Brand new process."

[Exit fully, then relaunch:]
```
omni
> what's my project codename and how often does the deploy key rotate?
```
> "It remembers — ZORBLAX-9, every 19 days. No context file, no copy-paste. A
> fresh session reading straight from the persistent memory."

---

## [1:35–2:25] ACT 2 — THE KILLER DEMO: memory survives file deletion

> "Most agents 'remember' by keeping files in the context window. Omni-Dev stores
> knowledge in a graph that's independent of your files. Watch this."

[Run:]
```
venv\Scripts\python proof_repo_memory.py
```
> "This script creates a source file with a made-up secret — a magic constant of
> 7321 and an audit table called 'glorptide'. It feeds the file into memory...
> then it DELETES the entire repo... and then it asks what was in the file."

[Wait for the verdict, point at it.]

> "PASS. The file is gone from disk, but the agent still recalls 7321 and
> glorptide. Those values are arbitrary — the only way it can know them is from
> the memory graph. The knowledge outlived the code. That's a real memory layer,
> not a context window."

---

## [2:25–2:55] ACT 3 — Truly model-agnostic

> "And none of this is locked to one vendor."
```
> /model
```
[Switch from Gemini to Claude — or to a local Ollama model.]
```
> recall what we worked on last session
```
> "Same agent, same memory — different brain. Gemini, Claude, GPT, Groq, Mistral,
> or a fully local Ollama model running offline. The memory engine follows
> whichever model I choose, automatically."

---

## [2:55–3:25] ACT 4 — Queryable codebase knowledge graph

> "Omni-Dev also builds a knowledge graph of your codebase."
```
> what depends on cognee_paths?
> why was a JSON fallback store chosen?
```
> "It answers structurally — actual dependencies, definitions, and even the
> recorded rationale behind design decisions. That's GraphRAG over the repo, not
> a text search."

---

## [3:25–3:55] PROOF + COMPARISON CLOSER

> "And it's real, not slideware."
```
venv\Scripts\python verify_memory.py
```
> "Five out of five lifecycle checks pass — graph write, graph read, and the
> offline mirror."

> "Compared to something like Claude Code: Claude Code is polished, but it forgets
> your project when the session ends and it locks you to one vendor. Omni-Dev
> gives *any* model a permanent, queryable memory of your codebase — and it
> physically can't lose your data, even if the cloud goes down or you delete the
> repo."

> "Omni-Dev. Any model. Permanent memory. Zero amnesia. Thanks for watching."

---

## IF SOMETHING GOES WRONG ON CAMERA

- **`Could not set lock on file ... Error: 33`** — another process holds the graph
  DB. Say: "Notice memory is still served from the offline mirror — it never loses
  data." Then close the other window and retry. (Turn the bug into a feature story.)
- **Slow first recall** — say: "First graph recall is a cold LLM call; that's why
  startup recall runs in the background." Then continue.
- **Model switch hangs** — you forgot to pre-warm it; cut and retry.

---

## ONE-LINER VARIANTS (for the title card / submission text)

- "A coding agent that never forgets — across sessions, models, and even repo deletion."
- "Any LLM. One permanent memory. Built on Cognee + LiteLLM."
- "Claude Code forgets when the session ends. Omni-Dev doesn't."

---

## CLAUDE CODE COMPARISON TABLE (optional on-screen slide)

| Dimension | Omni-Dev | Claude Code |
|---|---|---|
| Long-term memory | Persistent graph+vector; survives repo deletion | Context-window bound; static CLAUDE.md |
| Model choice | Any provider, switchable at runtime | Anthropic only |
| Local / offline | Local Ollama + JSON fallback | Requires Anthropic API |
| Codebase graph | Queryable GraphRAG (deps, defs, rationale) | Strong in-context, no persistent graph |
| Data-loss resistance | Offline mirror + repo-local storage | No independent store |
| UX / polish | Hackathon-stage CLI | Mature, IDE-integrated |

Be honest about the last row — owning the weakness makes the strengths land harder.
