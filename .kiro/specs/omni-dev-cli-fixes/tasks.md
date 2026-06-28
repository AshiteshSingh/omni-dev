# Implementation Plan: Omni-Dev CLI Fixes

## Overview

This plan ports and hardens the Omni-Dev Python CLI per the design. Work proceeds foundation-first: the Model Router, Tool Capability Policy, and Config Store come first (no dependencies), followed by agent validation and the text tool-call parser, then the redesigned agentic loop, the rendering/visual system, the permission system and persistent shell, transcript/history persistence, MCP support, structured diffs, cost/token warnings, utility commands and onboarding, and finally wiring everything into `interface.py` and `omni_dev.py`.

All model calls flow through a single injectable `completion_fn` so the entire test suite runs offline against a `FakeBackend`. Property-based tests use pytest + Hypothesis (`settings(max_examples=100)` minimum) and are tagged `# Feature: omni-dev-cli-fixes, Property <n>`. Each of the design's 34 correctness properties is implemented by one property-based test.

## Tasks

- [x] 1. Establish test scaffolding and offline fake backend
  - Create `tests/` package layout and `tests/conftest.py` with shared fixtures (temp config dir via `monkeypatch` on `USERPROFILE`/`HOME`, temp project dir)
  - Implement `tests/fakes.py` with `FakeBackend`: a scripted completion function returning queued responses, raising scripted errors, and recording call count + arguments (supports both streamed and non-streamed shapes)
  - Add Hypothesis strategies module `tests/strategies.py` for model identifiers, assistant content (prose + fenced code + embedded tool-call JSON + literal escapes), tool-call sequences, configs, file-content pairs, and call-record sequences
  - Add `pytest.ini`/`pyproject` pytest config and ensure `hypothesis` is in `requirements.txt`
  - _Requirements: 8.8_

- [x] 2. Implement the Model Router
  - [x] 2.1 Create `src/model_router.py` with `RouteDecision` dataclass and `normalize_model(raw)`
    - Implement normalization rules: trim quotes/whitespace, collapse repeated `/`, strip `model `/`models/` prefixes, map `ollama ` → `ollama/`, infer provider prefix for bare names, preserve Ollama size tags and cloud markers
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 2.2 Write property test for normalization canonical form and idempotence
    - **Property 1: Normalization is idempotent and produces canonical form**
    - **Validates: Requirements 5.1, 5.3, 5.4**

  - [ ]* 2.3 Write property test for cross-layer normalization agreement
    - **Property 2: Normalization agreement across layers**
    - **Validates: Requirements 5.2**

  - [x] 2.4 Implement `route(raw, env)` and `get_completion_fn()`
    - Build full `RouteDecision`: local vs cloud Ollama endpoint selection (`http://localhost:11434` vs `https://ollama.com`), `ollama_chat/` prefix for tool-enabled Ollama, API key/timeout resolution, and error when cloud Ollama has no key
    - Implement local-Ollama connectivity probe with single `ollama serve` retry and descriptive connectivity error; bounded timeout with descriptive timeout error naming the model
    - Expose injectable `get_completion_fn()` defaulting to `litellm.completion`
    - Add fallback to layer-local resolution when normalization raises
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.3, 5.2, 5.7_

  - [ ]* 2.5 Write property test for Ollama endpoint routing
    - **Property 3: Ollama endpoint routing**
    - **Validates: Requirements 1.3, 1.4, 5.5, 5.6**

  - [ ]* 2.6 Write property test for cloud Ollama without a key
    - **Property 4: Cloud Ollama without a key produces an error and sends no request** (assert `completion_fn` from FakeBackend is never invoked)
    - **Validates: Requirements 1.5**

  - [ ]* 2.7 Write example tests for timeout, auth, and permission error mapping
    - Cover request-timeout (1.1, 1.2), auth/API-key error (6.1), and permission/access error (6.2) message mapping using `FakeBackend` scripted errors
    - _Requirements: 1.1, 1.2, 6.1, 6.2_

- [x] 3. Implement the Tool Capability Policy
  - [x] 3.1 Create `src/tool_policy.py` with `NO_TOOL_MODELS`, `TOOL_CAPABLE`, and `supports_tools(route)`
    - Cloud providers enabled unless in deny list; local Ollama enabled by allow list/family heuristic; optimistic-True for unknown local models; remove `disable_tools_for_model`
    - _Requirements: 2.1, 2.2, 2.3_

  - [ ]* 3.2 Write property test for tool-capability decision and `ollama_chat/` prefix
    - **Property 5: Tool-capability decision and prefix**
    - **Validates: Requirements 2.1, 2.2, 2.3**

- [x] 4. Implement the Config Store
  - [x] 4.1 Create `src/config_store.py` with global + per-project JSON model
    - Implement `get_global_config`, `save_global_config`, `get_project_config`, `save_project_config` (keyed by absolute path), `Config_Defaults`, shallow merge of missing keys, atomic writes (temp + replace), and safe fallback on missing/corrupt files without deleting
    - Define Global/Project config shapes including `activeModel`, `allowedTools`, `history`, `hasTrustDialogAccepted`, MCP server entries, cost/token thresholds
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

  - [ ]* 4.2 Write property test for config round-trip
    - **Property 20: Config round-trip**
    - **Validates: Requirements 9.2, 9.3, 9.7, 9.8**

  - [ ]* 4.3 Write property test for corrupt/missing config fallback
    - **Property 21: Corrupt or missing config falls back to defaults safely** (assert existing file not deleted)
    - **Validates: Requirements 9.4, 9.5**

  - [ ]* 4.4 Write property test for missing-key merge
    - **Property 22: Missing keys merge with defaults**
    - **Validates: Requirements 9.6**

- [x] 5. Checkpoint - foundational services
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement agent input validation and the text tool-call parser
  - [x] 6.1 Create `src/agent/validation.py`
    - JSON-schema validation of model-generated args; per-tool value-level `validate_input` hook; produce `Input_Validation_Error` content flagged as error
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 6.2 Create `src/agent/tool_parser.py` with `extract_tool_calls` and `strip_tool_call_text`
    - Locate fenced ```json/```tool blocks and explicit `{"name","arguments"}` objects; return only calls whose name is in valid tools; robust to no-match; remove tool-call blocks from final text; replace and remove `_clean_final_text`
    - _Requirements: 3.5, 3.1_

  - [ ]* 6.3 Write property test for no tool-call leakage and preserved text
    - **Property 6: No tool-call leakage, legitimate text preserved**
    - **Validates: Requirements 3.1, 3.5, 3.7**

- [x] 7. Redesign the Agent Loop (`src/agent/core.py`)
  - [x] 7.1 Rebuild `OmniDevAgent.execute_task(prompt, progress_callback, abort_event)` core iteration
    - Route via Model Router + capability policy; call model via `completion_fn`; native tool-calls with text-parser fallback; iterate prior + assistant + appended results until a no-tool-call Final_Response; honor `MAX_ITERATIONS` with incompleteness notice (and no notice if a final arrives at the limit)
    - Retry once without tool schemas when a request is rejected for tools (2.4); handle empty Final_Response notice (6.4); surface routing error without calling backend
    - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 6.4_

  - [ ]* 7.2 Write property test for loop progression and termination
    - **Property 10: Loop progression and termination** (FakeBackend: N tool rounds then no-tool message)
    - **Validates: Requirements 2.5, 2.6, 2.7**

  - [ ]* 7.3 Write edge-case tests for max-iteration and empty/retry paths
    - Max-iteration with and without a final at the limit (2.8, 2.9); retry-without-tools (2.4); empty-response notice (6.4)
    - _Requirements: 2.4, 2.8, 2.9, 6.4_

  - [x] 7.4 Integrate validation, value-checks, and unknown-tool handling into the loop
    - Schema validation before execution; value-level check after schema; unknown-tool error `No such tool available: <name>`; append error results and continue
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 7.5 Write property test for schema validation precedes execution
    - **Property 11: Schema validation precedes execution** (assert tool `call` not invoked)
    - **Validates: Requirements 7.1, 7.2**

  - [ ]* 7.6 Write property test for value-level check rejection
    - **Property 12: Value-level check rejection**
    - **Validates: Requirements 7.3**

  - [ ]* 7.7 Write property test for unknown tool handling
    - **Property 13: Unknown tool handling**
    - **Validates: Requirements 7.4**

  - [x] 7.8 Implement concurrency partitioning, bounded concurrency, and ordered results
    - All-read-only → concurrent via `asyncio.Semaphore(10)` + `gather`; otherwise serial; collect with call index and re-sort to tool-call order before appending
    - _Requirements: 7.5, 7.6, 7.7_

  - [ ]* 7.9 Write property test for concurrency mode selection and bound
    - **Property 14: Concurrency mode selection and bound** (track simultaneous executions ≤ 10)
    - **Validates: Requirements 7.5, 7.6**

  - [ ]* 7.10 Write property test for ordered tool results
    - **Property 15: Tool results ordered to match call order** (scripted varying per-tool latency)
    - **Validates: Requirements 7.7**

  - [x] 7.11 Integrate permission gating, autonomous bypass, and error continuation
    - Submit each invocation to the injected `canUseTool` Permission_Check unless Autonomous_Mode; append denial error and continue; capture tool execution errors, append flagged result, and continue
    - _Requirements: 6.3, 7.8, 7.9, 7.10_

  - [ ]* 7.12 Write property test for permission gating vs Autonomous_Mode
    - **Property 16: Permission gating respects Autonomous_Mode**
    - **Validates: Requirements 7.8, 7.10**

  - [ ]* 7.13 Write property test for error append and loop continuation
    - **Property 17: Errors append a result and the loop continues**
    - **Validates: Requirements 6.3, 7.9**

  - [x] 7.14 Implement repeated-text-tool-call dedup, interrupt handling, and result truncation
    - Signature comparison across iterations to stop repeated Text_Tool_Calls; `asyncio.Event` interrupt checked before each model call and tool round, emitting a descriptive message and preserving consistent history; head/tail truncation at `MAX_TOOL_RESULT_CHARS` with omitted-count notice
    - _Requirements: 6.5, 7.11, 7.12, 7.13_

  - [ ]* 7.15 Write property test for repeated identical text tool-calls termination
    - **Property 18: Repeated identical text tool-calls terminate the loop**
    - **Validates: Requirements 6.5**

  - [ ]* 7.16 Write property test for oversized tool-result truncation
    - **Property 19: Oversized tool results are truncated head and tail**
    - **Validates: Requirements 7.13**

  - [ ]* 7.17 Write edge-case test for interrupt history consistency
    - Assert interrupt stops further calls, emits message, and leaves history able to accept a new request
    - _Requirements: 7.11, 7.12_

- [x] 8. Checkpoint - agent loop fidelity
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement the visual system and output renderer
  - [x] 9.1 Create `src/cli/theme.py`
    - Rich `Theme` (`OMNI_THEME`), glyphs with ASCII fallbacks, `format_tool_activity(tool_name, args, state)` single code path, message framing helpers, banner, status footer; centralize Windows UTF-8 enforcement and `legacy_windows=False`
    - _Requirements: 4.1, 4.2, 6.6_

  - [x] 9.2 Create `src/cli/render.py` escape normalization and final/non-stream render
    - `normalize_escapes` converting literal `\n`/`\t` outside fenced code while preserving fence boundaries/content; `render_final` single Markdown render with no artificial delay; themed error renderer; resilient cleaning that renders salvageable portions without raising
    - _Requirements: 3.2, 3.3, 3.4, 3.8, 4.4, 6.6_

  - [ ]* 9.3 Write property test for escape normalization preserving fenced code
    - **Property 7: Escape normalization preserves fenced code**
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 9.4 Write property test for render-never-fails on malformed input
    - **Property 8: Rendering never fails on malformed input**
    - **Validates: Requirements 3.8**

  - [x] 9.5 Implement streaming renderer `stream_response`
    - Consume litellm chunks; accumulate `delta.content` and re-render full buffer as Markdown per chunk (no per-word sleep); accumulate `delta.tool_calls` by index and return them; guarantee streamed result equals non-streamed render
    - _Requirements: 4.3, 4.5_

  - [ ]* 9.6 Write property test for streamed equals non-streamed output
    - **Property 9: Streamed output equals non-streamed output**
    - **Validates: Requirements 4.3, 4.5**

- [x] 10. Implement structured diff rendering
  - [x] 10.1 Add `render_diff(old, new, path, console, theme)` to `src/cli/render.py`
    - `difflib`-based hunks in a Rich panel with `diff.add`/`diff.del`/`diff.ctx` and context lines; new files render all lines added; diff text passes through escape normalization so no literal `\n`/raw tool JSON appears
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

  - [ ]* 10.2 Write property test for structured diff classification and context
    - **Property 31: Structured diff classifies and contextualizes changes**
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4**

- [x] 11. Implement the granular permission system (`src/permissions.py`)
  - [x] 11.1 Rebuild permissions with `SAFE_COMMANDS`, prefix parsing, and key derivation
    - `get_command_prefix` (with injection flag for `;`, `&&`, `||`, `|`, `$(...)`, backticks), `get_permission_key`, `has_permission` (autonomous bypass, blanket `run_command`, SAFE_COMMANDS, prefix match, injection→exact-match, file-edit session grant, allowedTools lookup or prompt), `save_permission` (persist except file-edit tools)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10_

  - [ ]* 11.2 Write property test for allowedTools and blanket grants
    - **Property 23: allowedTools and blanket grants authorize without prompting**
    - **Validates: Requirements 10.1, 10.2, 10.10**

  - [ ]* 11.3 Write property test for command-prefix permissions
    - **Property 24: Command-prefix permissions authorize matching commands**
    - **Validates: Requirements 10.3, 10.4**

  - [ ]* 11.4 Write property test for command-injection exact approval
    - **Property 25: Command injection requires exact prior approval**
    - **Validates: Requirements 10.5**

  - [ ]* 11.5 Write property test for file-edit session grant and autonomous authorization
    - **Property 26: File-edit approval grants session write without persisting**
    - **Validates: Requirements 10.7, 10.8**

- [x] 12. Implement the PersistentShell (`src/tools/persistent_shell.py`)
  - [x] 12.1 Create session-scoped `PersistentShell` backing `run_command`
    - Long-lived `powershell.exe` (fallback `cmd.exe`) on Windows / `bash` on POSIX; sentinel-marker output capture with stdout/stderr/exit code; cwd/env persistence across calls; bounded per-command timeout that terminates the command but keeps the shell usable; `interrupt()` and `kill()`
    - Wire `run_command` tool to use the PersistentShell
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [ ]* 12.2 Write property test for shell state persistence
    - **Property 27: Shell state persists across invocations** (local cross-platform commands + temp dir only)
    - **Validates: Requirements 11.2, 11.3, 11.4**

  - [ ]* 12.3 Write edge-case test for shell timeout and interrupt survivability
    - Assert timeout/interrupt terminate the command and the shell remains usable
    - _Requirements: 11.5, 11.6_

- [x] 13. Checkpoint - rendering, permissions, and shell
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Implement transcript store and command history
  - [x] 14.1 Create `src/transcript_store.py`
    - `save_transcript`/`list_transcripts`/`load_transcript`/`fork_transcript` under `<global>/transcripts/`; restore reproduces message order; fork copies prefix to a new id leaving original unchanged
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 14.2 Implement bounded command history in Project_Config
    - Prepend on submit, max 100, most-recent-first, discard oldest, never duplicate the most-recent entry; expose navigation order
    - _Requirements: 12.6, 12.7, 12.8_

  - [ ]* 14.3 Write property test for transcript save/restore round-trip
    - **Property 28: Transcript save/restore round-trip**
    - **Validates: Requirements 12.1, 12.2**

  - [ ]* 14.4 Write property test for fork prefix preservation
    - **Property 29: Fork preserves prefix and leaves original unchanged**
    - **Validates: Requirements 12.5**

  - [ ]* 14.5 Write property test for bounded, de-duplicated command history
    - **Property 30: Command history is bounded, most-recent-first, and de-duplicated**
    - **Validates: Requirements 12.6, 12.7, 12.8**

- [x] 15. Implement cost and token budget warnings
  - [x] 15.1 Extend `src/cost_tracker.py` with cumulative totals and threshold warnings
    - Track cumulative cost and input+output tokens; cost warning when over Cost_Threshold and unacknowledged; token warning when over token threshold; persist acknowledgement and suppress repeat cost warning in session; retain existing cost/token summary on request
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [ ]* 15.2 Write property test for cumulative totals
    - **Property 32: Cumulative totals equal the sum of calls**
    - **Validates: Requirements 15.1**

  - [ ]* 15.3 Write property test for threshold warnings and acknowledgement
    - **Property 33: Threshold warnings fire exactly when exceeded and respect acknowledgement**
    - **Validates: Requirements 15.2, 15.3, 15.4**

- [x] 16. Implement MCP client and tool/command registration
  - [x] 16.1 Create `src/mcp/client.py`
    - `connect_all(config)` with graceful per-server failure + notice; `register_tools` wrapping discovered tools as `MCPTool` adapting `BaseTool` (so they flow through validation/permission/ordering); `register_commands`; persist server approval in config
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 16.2 Write integration tests for MCP registration and graceful failure
    - Fake MCP connection: assert tools/commands registered, MCP tool runs through Requirement 7 path, and a failing server leaves remaining capabilities working with a notice
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [x] 17. Implement utility commands and first-run onboarding
  - [x] 17.1 Implement ported utility commands in `src/commands/`
    - `bug` (capture + store locally), `pr_comments` (via `gh`/`git`, descriptive error when unavailable/offline), `release-notes` (show changelog), `terminalSetup` (configure keybindings, persist to Global_Config), `clear` (reset conversation history), `resume` (list/resume/fork transcripts); `help` enumerates built-in, ported, and MCP commands
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.9_

  - [x] 17.2 Create `src/cli/onboarding.py` first-run trust prompt
    - Prompt when Project_Trust not accepted before processing tasks; persist acceptance to Project_Config so the prompt is not shown again
    - _Requirements: 16.7, 16.8_

  - [ ]* 17.3 Write property test for help listing every registered command
    - **Property 34: Help lists every registered command**
    - **Validates: Requirements 16.9**

  - [ ]* 17.4 Write example tests for utility commands and onboarding
    - `pr_comments` offline/missing-tooling error (mock `gh`/`git`), `bug` storage, `clear` reset, `terminalSetup` persistence, onboarding prompt + persistence
    - _Requirements: 16.1, 16.3, 16.5, 16.6, 16.7, 16.8_

- [x] 18. Wire components into the interface layer (`src/cli/interface.py`)
  - [x] 18.1 Integrate router, theme/renderer, permissions, transcript, history, cost footer, and onboarding into `interface.py`
    - Replace duplicated normalization with Model Router; route `/model` and provider sync through it; replace ASCII markers and word-by-word `render_smooth_markdown` with the themed renderer + streaming; supply interactive `canUseTool` approve/deny/remember callback; persist history; render status footer with model/branch/tokens/cost; wire Ctrl+C to the loop's `asyncio.Event`; trigger onboarding; render edits via `render_diff`; show active progress indicator during Ollama requests
    - _Requirements: 1.7, 3.1, 3.6, 4.1, 4.2, 5.2, 6.6, 10.1, 10.9, 12.4, 14.1, 15.5, 16.6, 16.7_

  - [x] 18.2 Wire startup and command dispatch in `omni_dev.py`
    - Load global/project config, run MCP `connect_all` at startup with graceful failure, register commands (built-in + ported + MCP), apply Project_Trust onboarding gate, and start the session
    - _Requirements: 9.1, 13.1, 16.9_

- [x] 19. Final integration and full-suite verification
  - [x] 19.1 Wire end-to-end agent path and resolve cross-module integration
    - Connect Model Router → loop → renderer → permissions → persistent shell → transcript/cost so a scripted task runs fully through the FakeBackend with no orphaned modules
    - _Requirements: 2.5, 2.6, 2.7, 7.7, 7.8_

  - [ ]* 19.2 Write integration test for a scripted end-to-end task
    - Drive `execute_task` through multiple tool rounds via FakeBackend asserting rendering, ordering, permission gating, and transcript persistence together
    - _Requirements: 8.4, 8.6, 8.7, 8.12_

  - [x] 19.3 Run the full test suite and CLI smoke check
    - Run `pytest -q` (all property and example tests pass offline) and a CLI import/startup smoke check (`python -c "import omni_dev"` and a non-interactive help invocation); fix any failures
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12_

- [x] 20. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP, but they implement the design's correctness properties and Requirement 8's test mandates.
- Each property test is tagged `# Feature: omni-dev-cli-fixes, Property <n>` and uses Hypothesis with `settings(max_examples=100)` minimum.
- All tests run offline via the injected `FakeBackend` (Requirement 8.8); the shell property test uses only local cross-platform commands in a temp directory (Requirement 8.11).
- Coverage cross-check: Requirements 1–16 are each referenced by at least one task, and all 34 design properties are each implemented by a dedicated property-test sub-task (Properties 1–5 routing/policy; 6–9 rendering; 10–19 loop; 20–22 config; 23–26 permissions; 27 shell; 28–30 transcript/history; 31 diff; 32–33 cost; 34 commands).
- Requirement 13 (MCP) and the `pr_comments`/`bug`/`terminalSetup`/onboarding commands are covered by example/integration tests rather than properties, per the design's Testing Strategy.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.2", "4.2", "4.3", "4.4"] },
    { "id": 3, "tasks": ["2.5", "2.6", "2.7", "6.1", "6.2"] },
    { "id": 4, "tasks": ["6.3", "7.1", "9.1"] },
    { "id": 5, "tasks": ["7.2", "7.3", "7.4", "9.2", "10.1", "11.1", "12.1"] },
    { "id": 6, "tasks": ["7.5", "7.6", "7.7", "7.8", "9.3", "9.4", "9.5", "10.2", "11.2", "11.3", "11.4", "11.5", "12.2", "12.3"] },
    { "id": 7, "tasks": ["7.9", "7.10", "7.11", "9.6", "14.1", "14.2", "15.1"] },
    { "id": 8, "tasks": ["7.12", "7.13", "7.14", "14.3", "14.4", "14.5", "15.2", "15.3", "16.1"] },
    { "id": 9, "tasks": ["7.15", "7.16", "7.17", "16.2", "17.1", "17.2"] },
    { "id": 10, "tasks": ["17.3", "17.4", "18.1"] },
    { "id": 11, "tasks": ["18.2", "19.1"] },
    { "id": 12, "tasks": ["19.2", "19.3"] }
  ]
}
```
