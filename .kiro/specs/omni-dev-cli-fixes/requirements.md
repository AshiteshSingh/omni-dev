# Requirements Document

## Introduction

Omni-Dev is a Python interactive CLI coding agent (a port of a TypeScript anon-kode / Claude Code style tool) that drives an agentic tool-use loop over `litellm` with `cognee`-backed memory. The application has three user-reported problems:

1. **Unreliable Ollama operation** — when using a local Ollama model the CLI stalls mid-task and dumps garbled output (raw HTML/Vue source with literal `\n` characters) to the terminal instead of clean formatted text.
2. **Unprofessional appearance** — the interface uses ASCII markers (`[READ]`, `[CMD]`, etc.) and a fake word-by-word `Live` animation that feels janky.
3. **Incomplete agentic behavior** — the CLI does not deliver the full agentic tool-use behavior of the reference TypeScript implementation, primarily because tool/function-calling is blanket-disabled for all local Ollama models.

This specification defines the requirements to make Ollama (local and cloud) operation reliable, correctly enable agentic tool-use for capable models, render agent output cleanly and correctly, present a polished professional terminal UI, handle errors robustly, and make the agent loop verifiable through automated tests.

The root causes identified in the existing code (`src/agent/core.py`, `src/cli/interface.py`) that these requirements address are:
- `disable_tools_for_model` disables tool-calling for **all** local Ollama models, even tool-capable ones.
- A fragile hand-rolled text-based JSON tool-call parser (balanced-brace scanner + regex repair) that misfires and leaks raw JSON/tool arguments into the UI, propped up by a `_clean_final_text` band-aid post-processor.
- Duplicated, overlapping model-name normalization logic split across `interface.py` and `core.py`.
- A `render_smooth_markdown` function that fakes streaming by replaying an already-computed response word-by-word with `time.sleep`, which is slow and can mangle markdown/code fences.
- No automated tests for the agent loop or Ollama routing.

### Scope Expansion: Porting Portable Reference Functionality

Beyond the three reported problems, this specification expands scope to port the remaining **portable** functionality of the reference TypeScript implementation (`scratch_repo`) into the Python `litellm` + `cognee` port. The goal is feature parity for everything that is not tied to Anthropic-proprietary backends or services. The reference behaviors already present in the Python port (the agentic loop; the `agent/subagent`, `architect`, `bash`, `file_read`, `file_write`, `file_edit`, `glob`, `grep`, `ls`, `notebook` read/edit, `think`, and `memory` tools; and the `/compact`, `/config`, `/ctx_viz`, `/doctor`, `/init`, `/review`, `/model`, `/api_key`, `/cost`, `/tokens`, `/history`, `/commit`, `/memory`, `/index`, `/clear`, and `/autonomous` commands) are not re-specified here, though Requirements 1–8 may refine them. Requirements 9 through 16 add the missing portable feature areas: a persistent configuration system, a granular persistent tool-permission system, a stateful persistent shell, conversation persistence and resume, Model Context Protocol (MCP) support, structured diff rendering, cost/token budget warnings, and additional utility slash-commands plus first-run onboarding.

### Out of Scope (Anthropic-Proprietary Features Intentionally Excluded)

The following reference features depend on Anthropic-proprietary backends or services and are intentionally excluded from this port. Where a reference behavior depends on one of these (for example, binary-feedback comparison inside the query loop), the Python port simply omits that branch:

- **Anthropic OAuth login/logout** — the `login`/`logout` console OAuth flow and Anthropic account management.
- **Statsig telemetry and experiments** — feature gates, experiment checks, and event logging.
- **Sentry error reporting** — remote crash/error reporting.
- **Sticker-request tool** — the physical-sticker request feature.
- **Binary-feedback A/B comparison** — the `BinaryFeedback` model-response comparison flow.
- **macOS "listen"/dictation command** — the `listen` speech-dictation command.
- **npm-package auto-updater** — the `AutoUpdater` self-update mechanism for the npm distribution.

## Glossary

- **Omni_Dev_CLI**: The interactive command-line application, comprising the interface layer (`src/cli/interface.py`) and the agent engine (`src/agent/core.py`).
- **Agent_Loop**: The iterative request/tool-execution/response cycle in `OmniDevAgent.execute_task` that calls the model, executes any returned tool calls, feeds results back, and repeats until a final answer is produced.
- **Model_Router**: The component responsible for normalizing a user-supplied model identifier into a canonical `provider/model` form and selecting the correct provider configuration (including Ollama local vs. cloud routing).
- **Tool_Capability_Policy**: The logic that decides, for a given model, whether native tool/function-calling schemas are sent to the model.
- **Native_Tool_Call**: A structured tool call returned by the model through the provider's function-calling API (`response.choices[].message.tool_calls`).
- **Text_Tool_Call**: A tool call emitted by a model as text inside the message content rather than through the native function-calling API.
- **Output_Renderer**: The interface component that displays the agent's intermediate and final output to the terminal.
- **Local_Ollama_Model**: An Ollama model served from a local Ollama instance (default base URL `http://localhost:11434`).
- **Cloud_Ollama_Model**: An Ollama model served from the hosted Ollama API (base URL `https://ollama.com`), indicated by a `cloud` marker in the model name or a cloud API base.
- **Tool_Capable_Model**: A model that supports native function-calling, including modern local Ollama models such as `llama3.1`, `qwen2.5`, and `mistral-nemo`.
- **Progress_Event**: A notification emitted from the Agent_Loop to the interface describing an in-progress action (assistant message, thinking, or a specific tool invocation).
- **Final_Response**: The agent's last assistant message returned to the user after the Agent_Loop completes. In the reference implementation this is the assistant message that contains no tool-use blocks, which terminates the recursive loop.
- **Garbled_Output**: Terminal output containing raw JSON tool-call structures, literal escape sequences such as `\n`, or broken markdown/code fences shown to the user instead of clean formatted text.
- **Tool_Input_Validation**: The step that validates model-generated tool arguments against a tool's declared input schema (and any per-tool value-level checks) before the tool is executed, analogous to the reference implementation's `zod` `safeParse` and optional `validateInput`.
- **Input_Validation_Error**: The structured, error-flagged tool result appended to the conversation history when Tool_Input_Validation fails, fed back to the model instead of executing the tool or crashing.
- **Permission_Check**: The gate (reference `canUseTool`) that authorizes or denies a tool invocation before execution.
- **Autonomous_Mode**: A configured mode (reference `dangerouslySkipPermissions`; the Python application's `OMNI_AUTONOMOUS` mode) that bypasses the Permission_Check for all tool invocations.
- **Read_Only_Tool**: A tool that does not mutate state and is therefore eligible for concurrent execution with other read-only tools.
- **Tool_Concurrency_Limit**: The maximum number of Read_Only_Tool invocations executed concurrently in a single round of tool execution (reference value: 10).
- **Ordered_Tool_Results**: Tool results re-ordered to match the original order of the model's tool calls before being appended to the conversation history.
- **Interrupt**: A user-initiated cancellation (reference abort signal; the Python CLI's Ctrl+C) that stops the Agent_Loop from issuing further model or tool calls and yields a descriptive interrupt message.
- **Unknown_Tool**: A tool name requested by the model that does not correspond to any registered tool.
- **Global_Config**: The user-level configuration persisted on disk (reference `getGlobalConfig`/`saveGlobalConfig`, stored in the global config file), holding cross-project settings such as the active model, onboarding state, verbosity, notification preferences, and cost-threshold acknowledgement.
- **Project_Config**: The per-project configuration persisted on disk (reference `getCurrentProjectConfig`/`saveCurrentProjectConfig`), keyed by the project's absolute path, holding project-scoped settings such as command history, Allowed_Tools, MCP server entries, and project trust state.
- **Config_Defaults**: The default configuration values applied when a config file is absent, unreadable, or fails to parse (reference `DEFAULT_GLOBAL_CONFIG` / `DEFAULT_PROJECT_CONFIG`).
- **Allowed_Tools**: The list, persisted in the Project_Config, of tool-permission keys the user has approved (reference `allowedTools`), used by the Permission_Check to authorize tool invocations without re-prompting.
- **Command_Prefix_Permission**: A Bash/run_command permission expressed as a command prefix in the form `<tool>(<prefix>:*)` (for example `git commit:*`) that authorizes any command sharing that verified prefix.
- **Safe_Command**: A command on the built-in allowlist that never requires approval (reference `SAFE_COMMANDS`: `git status`, `git diff`, `git log`, `git branch`, `pwd`, `tree`, `date`, `which`).
- **Command_Injection**: A bash command containing chained or substituted subcommands (for example via `;`, `&&`, `|`, or `$(...)`) whose prefix cannot be safely verified, requiring an exact prior approval to run (reference `commandInjectionDetected`).
- **Command_Prefix**: The leading subcommand prefix extracted from a bash command used to match a Command_Prefix_Permission (reference `getCommandSubcommandPrefix`).
- **Persistent_Shell**: A stateful shell process backing the run_command tool that preserves working directory and environment variables across successive command invocations within a session (reference `PersistentShell`), capturing stdout, stderr, and exit code per command under a bounded timeout.
- **Conversation_Transcript**: A persisted record of a conversation's messages on disk, identified for later listing, resuming, or forking (reference `resume` command and fork-conversation behavior).
- **Command_History**: The bounded, ordered list of prior user inputs persisted in the Project_Config (reference `history`, maximum 100 entries) and navigable across sessions.
- **MCP_Server**: A Model Context Protocol server configured in the Global_Config or Project_Config that the CLI connects to in order to discover tools and commands.
- **MCP_Tool**: A tool discovered from an MCP_Server and registered so that it participates in the Agent_Loop like a native tool.
- **Structured_Diff**: A colorized, hunk-based rendering of the difference between a file's previous and new contents, showing changed lines with surrounding line context (reference `StructuredDiff` / `FileEditTool` result rendering).
- **Cost_Threshold**: A configurable cumulative session-cost limit that, when exceeded, triggers a warning to the user (reference `CostThresholdDialog`).
- **Token_Warning**: A warning surfaced when cumulative session token usage exceeds a configurable threshold (reference `TokenWarning`).
- **Project_Trust**: The per-project flag recording that the user has accepted the first-run trust prompt for the project directory (reference `hasTrustDialogAccepted`).

## Requirements

### Requirement 1: Reliable Ollama Operation Without Stalling

**User Story:** As a user running a local or cloud Ollama model, I want the CLI to complete tasks without hanging or stalling mid-task, so that I can rely on Ollama for agentic work.

#### Acceptance Criteria

1. WHEN a request is sent to an Ollama model, THE Omni_Dev_CLI SHALL apply a bounded request timeout and return either a Final_Response or a descriptive error within that timeout.
2. IF an Ollama request exceeds the configured timeout, THEN THE Omni_Dev_CLI SHALL terminate the pending request and return a descriptive timeout error identifying the model.
3. WHEN routing a request to a Local_Ollama_Model, THE Model_Router SHALL target the local Ollama base URL `http://localhost:11434` unless an explicit local base URL is configured.
4. WHEN routing a request to a Cloud_Ollama_Model, THE Model_Router SHALL target the hosted Ollama base URL `https://ollama.com` and include the configured Ollama API key.
5. IF a Cloud_Ollama_Model is selected and no Ollama API key is configured, THEN THE Omni_Dev_CLI SHALL return a descriptive error instructing the user to set the key, without sending the request.
6. IF the local Ollama server cannot be reached, THEN THE Omni_Dev_CLI SHALL attempt to start the local Ollama server once and, IF the server still cannot be reached, SHALL return a descriptive connectivity error that names the model and states how to start the local server or switch providers.
7. WHILE the Agent_Loop is executing an Ollama request, THE Omni_Dev_CLI SHALL display an active progress indicator until the request completes or fails.

### Requirement 2: Correct Agentic Tool-Use Enablement

**User Story:** As a user, I want the CLI to use tools agentically with any tool-capable model, including modern local Ollama models, so that the agent can read files, run commands, and complete multi-step tasks.

#### Acceptance Criteria

1. WHEN selecting a Tool_Capable_Model, THE Tool_Capability_Policy SHALL enable native tool-calling schemas for that model regardless of whether the model is served locally or in the cloud.
2. WHERE a model is known to lack function-calling support, THE Tool_Capability_Policy SHALL disable native tool-calling schemas for that model.
3. WHEN tool-calling is enabled for an Ollama model, THE Model_Router SHALL use the `ollama_chat/` provider prefix for that request.
4. IF a model rejects a request that includes tool schemas, THEN THE Omni_Dev_CLI SHALL retry the request once without tool schemas and return the resulting response.
5. WHEN the model returns one or more Native_Tool_Calls, THE Agent_Loop SHALL execute each requested tool and append each tool result to the conversation history before issuing the next model request. (See Requirement 7 for ordering, validation, permission, and concurrency behavior.)
6. WHILE the Agent_Loop has not produced a Final_Response and has not reached the maximum iteration count, THE Agent_Loop SHALL re-invoke the model with the prior messages, the assistant message, and the appended tool results, and SHALL continue this cycle until the model returns an assistant message containing no tool calls.
7. WHEN the model returns an assistant message that contains no tool calls, THE Agent_Loop SHALL treat that message as the Final_Response and stop issuing further model requests.
8. WHEN the Agent_Loop reaches the maximum iteration count without a Final_Response, THE Agent_Loop SHALL stop without issuing another model request and THE Omni_Dev_CLI SHALL return a descriptive message indicating the task may be incomplete.
9. IF the Agent_Loop produces a Final_Response at the maximum iteration count, THEN THE Omni_Dev_CLI SHALL return that Final_Response without the incompleteness message.

### Requirement 3: Clean and Correct Output Rendering

**User Story:** As a user, I want the agent's output to be clean and correctly formatted, so that I never see raw JSON, literal escape characters, or broken code blocks.

#### Acceptance Criteria

1. WHEN displaying a Final_Response, THE Output_Renderer SHALL render text without exposing raw Native_Tool_Call or Text_Tool_Call JSON structures.
2. WHEN displaying any agent output that contains literal escape sequences such as `\n` or `\t`, THE Output_Renderer SHALL render those sequences as their corresponding formatting rather than as literal characters, regardless of whether the surrounding content is prose or an inline code example.
3. WHEN displaying agent output that contains fenced code blocks, THE Output_Renderer SHALL preserve each code block's content and fence boundaries intact.
4. WHEN displaying agent output that contains markdown, THE Output_Renderer SHALL render the markdown formatting.
5. IF the model emits a tool call as a Text_Tool_Call, THEN THE Agent_Loop SHALL execute the tool call and SHALL exclude the tool-call text from the Final_Response shown to the user.
6. IF a Text_Tool_Call fails to execute, THEN THE Omni_Dev_CLI SHALL display a descriptive tool-execution error to the user.
7. WHEN the model produces a final answer that contains no tool calls, THE Output_Renderer SHALL display the answer content unchanged except for formatting and SHALL NOT remove or truncate legitimate answer text.
8. IF agent output contains multiple formatting issues and not all can be corrected, THEN THE Output_Renderer SHALL render the portions that can be cleaned rather than failing the entire render.

### Requirement 4: Professional Terminal Interface

**User Story:** As a user, I want a polished, professional-looking interface, so that the tool feels trustworthy and pleasant to use.

#### Acceptance Criteria

1. WHEN announcing a tool invocation as a Progress_Event, THE Output_Renderer SHALL display a consistent, styled visual treatment for that tool action.
2. THE Output_Renderer SHALL present tool action labels using a single consistent visual style across all tools.
3. WHEN streaming model output is available, THE Output_Renderer SHALL display each generated token immediately as it is received from the model without batching tokens.
4. WHERE token-level streaming is unavailable for the selected model, THE Output_Renderer SHALL display the Final_Response in a single formatted render without artificial per-word delays and without simulated real-time token display.
5. WHEN rendering streamed output that contains markdown or code blocks, THE Output_Renderer SHALL produce the same final formatted result as a non-streamed render of the same content.

### Requirement 5: Consolidated Model Name Normalization

**User Story:** As a user, I want to enter a model name in any reasonable form and have it resolved correctly, so that provider routing is predictable and consistent.

#### Acceptance Criteria

1. THE Model_Router SHALL normalize a user-supplied model identifier into a canonical `provider/model` form using a single normalization component.
2. WHEN the interface layer and the agent engine resolve the same model identifier, THE Model_Router SHALL produce identical canonical results for both by using one shared normalization component as the authoritative source.
3. WHEN a model identifier omits a recognizable provider prefix, THE Model_Router SHALL infer a provider prefix from the model identifier.
4. WHEN a model identifier contains redundant separators or surrounding quotes or whitespace, THE Model_Router SHALL remove them before resolving the provider.
5. WHEN a Local_Ollama_Model identifier includes a size tag, THE Model_Router SHALL preserve the model identity required to address the local model.
6. WHEN a Cloud_Ollama_Model identifier includes a cloud marker, THE Model_Router SHALL preserve the cloud marker and route the request to the hosted Ollama API.
7. IF the shared normalization component fails to produce a result, THEN THE Model_Router SHALL fall back to each layer's local resolution, accepting that the interface layer and agent engine may produce different canonical results until the shared component recovers.

### Requirement 6: Robust Error Handling and Recovery

**User Story:** As a user, I want clear, actionable errors when something goes wrong, so that I can recover without restarting or guessing.

#### Acceptance Criteria

1. IF a model request fails with an authentication or API-key error, THEN THE Omni_Dev_CLI SHALL return a descriptive error naming the model and the action needed to provide a valid key.
2. IF a model request fails with a permission or access error, THEN THE Omni_Dev_CLI SHALL return a descriptive error naming the model and the access problem.
3. IF a tool invocation raises an error during execution, THEN THE Agent_Loop SHALL capture the error, append a descriptive tool-error result to the conversation history, and continue the loop.
4. IF the model returns an empty Final_Response, THEN THE Omni_Dev_CLI SHALL display a descriptive notice and suggest a recovery action.
5. WHEN the same set of Text_Tool_Calls is produced in consecutive iterations, THE Agent_Loop SHALL stop repeating those calls and return a Final_Response.
6. WHEN any error message is displayed to the user, THE Output_Renderer SHALL render it using the professional visual style defined for the interface.

### Requirement 7: Core Agentic Loop Fidelity

**User Story:** As a user porting the reference TypeScript agentic loop to Python, I want the Python Agent_Loop to faithfully mirror the reference loop's validation, ordering, concurrency, permission, unknown-tool, interrupt, and truncation behavior, so that the agent behaves predictably and safely across multi-step tasks.

#### Acceptance Criteria

1. WHEN the model requests a tool invocation, THE Agent_Loop SHALL perform Tool_Input_Validation of the model-generated arguments against that tool's declared input schema before executing the tool.
2. IF Tool_Input_Validation fails, THEN THE Agent_Loop SHALL append an Input_Validation_Error tool result flagged as an error to the conversation history, SHALL NOT execute the tool, and SHALL continue the loop.
3. WHERE a tool defines a value-level input check, THE Agent_Loop SHALL run that check after schema validation and, IF the check rejects the call, SHALL append the check's descriptive error result flagged as an error and SHALL NOT execute the tool.
4. IF the model requests an Unknown_Tool, THEN THE Agent_Loop SHALL append a tool result flagged as an error with the message `No such tool available: <name>` and SHALL continue the loop.
5. WHEN every tool requested in a single round is a Read_Only_Tool, THE Agent_Loop SHALL execute those tool invocations concurrently, otherwise THE Agent_Loop SHALL execute the requested tool invocations serially.
6. WHILE executing Read_Only_Tool invocations concurrently, THE Agent_Loop SHALL limit the number of simultaneously executing invocations to the Tool_Concurrency_Limit.
7. WHEN appending tool results to the conversation history after a round of tool execution, THE Agent_Loop SHALL order the results as Ordered_Tool_Results matching the order of the model's tool calls.
8. WHILE Autonomous_Mode is disabled, THE Agent_Loop SHALL submit each tool invocation to the Permission_Check before executing the tool.
9. IF the Permission_Check denies a tool invocation, THEN THE Agent_Loop SHALL append a tool result flagged as an error containing the denial reason and SHALL continue the loop without executing that tool.
10. WHILE Autonomous_Mode is enabled, THE Agent_Loop SHALL bypass the Permission_Check and execute each validated tool invocation.
11. WHEN an Interrupt is received while the Agent_Loop is awaiting a model response or executing tools, THE Agent_Loop SHALL stop issuing further model requests and tool invocations and SHALL emit a descriptive interrupt message.
12. WHEN an Interrupt is received, THE Omni_Dev_CLI SHALL preserve the conversation history in a consistent state that permits issuing a subsequent request.
13. IF a tool result content exceeds the configured maximum length, THEN THE Agent_Loop SHALL truncate the content to that bounded size by retaining the head and tail and inserting a notice of the number of characters omitted before appending it to the conversation history.

### Requirement 8: Verifiable Agent Loop and Routing

**User Story:** As a developer maintaining Omni-Dev, I want automated tests for the agent loop and Ollama routing, so that regressions in tool-use, routing, and rendering are caught.

#### Acceptance Criteria

1. THE Omni_Dev_CLI SHALL provide automated tests that verify the Model_Router resolves representative model identifiers to their expected canonical `provider/model` form.
2. THE Omni_Dev_CLI SHALL provide automated tests that assert the Tool_Capability_Policy enables tool-calling for Tool_Capable_Models and disables it for models known to lack function-calling support, and those tests SHALL pass only when the policy behaves correctly.
3. THE Omni_Dev_CLI SHALL provide automated tests that assert the Output_Renderer produces output free of raw tool-call JSON and literal escape sequences for representative agent outputs, and those tests SHALL pass only when the rendered output is clean.
4. THE Omni_Dev_CLI SHALL provide automated tests that assert the Agent_Loop executes tool calls and appends their results to the conversation history using a stubbed model backend, and those tests SHALL pass only when execution and appending occur.
5. THE Omni_Dev_CLI SHALL provide automated tests that assert the Agent_Loop appends an Input_Validation_Error result and does not execute the tool when model-generated arguments fail Tool_Input_Validation, using a stubbed model backend, and those tests SHALL pass only when the rejection behavior occurs.
6. THE Omni_Dev_CLI SHALL provide automated tests that assert the Agent_Loop produces Ordered_Tool_Results matching the model's tool-call order when multiple Read_Only_Tool invocations are executed concurrently, using a stubbed model backend, and those tests SHALL pass only when the results are correctly ordered.
7. THE Omni_Dev_CLI SHALL provide automated tests that assert the Agent_Loop submits tool invocations to the Permission_Check when Autonomous_Mode is disabled and bypasses the Permission_Check when Autonomous_Mode is enabled, using a stubbed model backend, and those tests SHALL pass only when the permission behavior matches the Autonomous_Mode setting.
8. WHEN the test suite is executed, THE automated tests SHALL run without requiring a live network connection to any model provider.
9. THE Omni_Dev_CLI SHALL provide automated tests that assert writing then reading a Global_Config and a Project_Config round-trips the persisted settings, and that reading a missing or corrupt config file returns Config_Defaults without raising, and those tests SHALL pass only when round-trip and fallback behavior are correct.
10. THE Omni_Dev_CLI SHALL provide automated tests that assert the bash permission logic grants Safe_Commands without approval, grants commands matching an approved Command_Prefix_Permission, and requires exact prior approval when Command_Injection is detected, and those tests SHALL pass only when each case is decided correctly.
11. THE Omni_Dev_CLI SHALL provide automated tests that assert the Persistent_Shell preserves working-directory and environment-variable changes across successive command invocations within a session, using local commands only, and those tests SHALL pass only when state persists across commands.
12. THE Omni_Dev_CLI SHALL provide automated tests that assert saving a Conversation_Transcript and then restoring it reproduces the saved messages in order, and those tests SHALL pass only when the restored transcript matches the saved transcript.

### Requirement 9: Persistent Configuration System

**User Story:** As a user, I want my settings to persist across sessions at both the user level and the per-project level, so that the CLI remembers my preferences and project state without reconfiguration.

#### Acceptance Criteria

1. THE Omni_Dev_CLI SHALL persist a Global_Config to a user-level file on disk and a Project_Config keyed by the project's absolute path.
2. WHEN the Global_Config is saved and subsequently loaded, THE Omni_Dev_CLI SHALL return settings equal to the saved settings.
3. WHEN the Project_Config is saved and subsequently loaded, THE Omni_Dev_CLI SHALL return settings equal to the saved settings.
4. IF a configuration file does not exist, THEN THE Omni_Dev_CLI SHALL return Config_Defaults without raising an error.
5. IF a configuration file exists but cannot be parsed, THEN THE Omni_Dev_CLI SHALL return Config_Defaults without raising an error and without deleting the existing file.
6. WHEN loading a configuration file that omits one or more known keys, THE Omni_Dev_CLI SHALL supply Config_Defaults for the missing keys while preserving the stored values for the present keys.
7. THE Project_Config SHALL store the active model, Command_History, Allowed_Tools, and Project_Trust state.
8. WHEN the active model is changed through the configuration, THE Omni_Dev_CLI SHALL persist the new active model to the configuration so that a subsequent session loads it.

### Requirement 10: Granular Persistent Tool Permission System

**User Story:** As a user, I want fine-grained control over which tools and which commands the agent may run, with approvals remembered per project, so that I can work safely without re-approving every action or resorting to a single all-or-nothing toggle.

#### Acceptance Criteria

1. WHEN a tool invocation requires permission and Autonomous_Mode is disabled, THE Permission_Check SHALL authorize the invocation only if a matching entry exists in the project's Allowed_Tools, otherwise THE Omni_Dev_CLI SHALL prompt the user to approve or deny the invocation.
2. WHEN the run_command tool is invoked with a Safe_Command, THE Permission_Check SHALL authorize the invocation without prompting the user.
3. WHEN the user approves a run_command invocation by Command_Prefix, THE Omni_Dev_CLI SHALL record a Command_Prefix_Permission of the form `<tool>(<prefix>:*)` in the project's Allowed_Tools.
4. WHEN a run_command command's verified Command_Prefix matches a Command_Prefix_Permission in Allowed_Tools, THE Permission_Check SHALL authorize the command without prompting the user.
5. IF a run_command command contains Command_Injection, THEN THE Permission_Check SHALL authorize the command only when an exact match for that command exists in Allowed_Tools, otherwise THE Omni_Dev_CLI SHALL prompt the user.
6. WHEN the user approves a tool invocation and chooses to remember the approval, THE Omni_Dev_CLI SHALL persist the corresponding permission key to the project's Allowed_Tools.
7. WHEN the user approves an invocation of a file-editing tool (file_write, file_edit, or notebook_edit), THE Omni_Dev_CLI SHALL grant write permission for the session's original directory without persisting a permission entry to the Project_Config.
8. WHILE Autonomous_Mode is enabled, THE Permission_Check SHALL authorize every tool invocation without prompting the user.
9. IF the user denies a prompted tool invocation, THEN THE Omni_Dev_CLI SHALL report the denial to the Agent_Loop so that the invocation is not executed.
10. WHEN run_command is granted blanket permission by its tool name appearing in Allowed_Tools, THE Permission_Check SHALL authorize all run_command invocations without prompting the user.

### Requirement 11: Persistent Stateful Shell for Command Execution

**User Story:** As a user, I want shell commands run by the agent to share state within a session, so that changing directory or setting an environment variable in one command carries over to the next, matching how a real terminal behaves.

#### Acceptance Criteria

1. THE run_command tool SHALL execute commands through a Persistent_Shell that is reused across successive invocations within a session.
2. WHEN a command changes the working directory, THE Persistent_Shell SHALL apply that working directory to subsequent commands in the same session.
3. WHEN a command sets or modifies an environment variable, THE Persistent_Shell SHALL make that variable available to subsequent commands in the same session.
4. WHEN a command completes, THE Persistent_Shell SHALL return the command's standard output, standard error, and exit code.
5. THE Persistent_Shell SHALL apply a bounded per-command timeout and, IF a command exceeds that timeout, SHALL terminate the running command, return a descriptive timeout indication, and remain usable for subsequent commands.
6. WHEN an Interrupt is received while a command is executing, THE Persistent_Shell SHALL terminate the running command and remain usable for subsequent commands.

### Requirement 12: Conversation Persistence, Resume, and Fork

**User Story:** As a user, I want my conversations and input history saved so that I can resume a previous conversation, fork a new one from an earlier message, and recall earlier inputs across sessions.

#### Acceptance Criteria

1. WHEN a conversation produces messages, THE Omni_Dev_CLI SHALL persist the Conversation_Transcript to disk.
2. WHEN a Conversation_Transcript is saved and subsequently restored, THE Omni_Dev_CLI SHALL reproduce the saved messages in their original order.
3. THE Omni_Dev_CLI SHALL provide a command that lists previously persisted Conversation_Transcripts available for resuming.
4. WHEN the user resumes a selected Conversation_Transcript, THE Omni_Dev_CLI SHALL load that transcript's messages as the current conversation history.
5. WHEN the user forks a conversation from a selected earlier message, THE Omni_Dev_CLI SHALL start a new conversation containing the messages up to and including the selected message, leaving the original transcript unchanged.
6. WHEN a user submits an input, THE Omni_Dev_CLI SHALL prepend it to the persisted Command_History and SHALL retain at most 100 entries by discarding the oldest entries beyond that limit.
7. WHEN a user submits an input identical to the most recent Command_History entry, THE Omni_Dev_CLI SHALL NOT add a duplicate entry.
8. WHILE the user navigates Command_History in the interface, THE Omni_Dev_CLI SHALL present persisted entries from most recent to oldest.

### Requirement 13: Model Context Protocol (MCP) Support

**User Story:** As a user, I want the CLI to connect to configured MCP servers and use their tools and commands, so that I can extend the agent with external capabilities.

#### Acceptance Criteria

1. WHERE one or more MCP_Servers are configured in the Global_Config or Project_Config, THE Omni_Dev_CLI SHALL attempt to connect to each configured MCP_Server at startup.
2. WHEN a connection to an MCP_Server succeeds, THE Omni_Dev_CLI SHALL discover that server's tools and register each as an MCP_Tool that participates in the Agent_Loop like a native tool.
3. WHEN a connection to an MCP_Server succeeds and the server exposes commands, THE Omni_Dev_CLI SHALL register those commands as available slash-commands.
4. IF a connection to an MCP_Server fails, THEN THE Omni_Dev_CLI SHALL continue operating with the remaining tools and commands and SHALL surface a descriptive notice without terminating.
5. WHEN the model requests an MCP_Tool, THE Agent_Loop SHALL execute the tool through its MCP_Server and append the result to the conversation history following the same validation, permission, and ordering behavior defined in Requirement 7.
6. WHERE an MCP_Server requires enabling or approval before use, THE Omni_Dev_CLI SHALL provide a mechanism to enable or approve that server and SHALL persist the approval decision in the configuration.

### Requirement 14: Structured Diff Rendering for File Edits

**User Story:** As a user, I want file changes shown as a clear, colorized diff, so that I can quickly understand what an edit changed.

#### Acceptance Criteria

1. WHEN a file-editing tool (file_write, file_edit, or notebook_edit) changes a file, THE Output_Renderer SHALL display a Structured_Diff of the file's previous and new contents.
2. WHEN rendering a Structured_Diff, THE Output_Renderer SHALL visually distinguish added lines from removed lines.
3. WHEN rendering a Structured_Diff, THE Output_Renderer SHALL include surrounding unchanged lines as context around each changed hunk.
4. IF a file-editing tool creates a new file, THEN THE Output_Renderer SHALL render the new file's contents as added lines.
5. WHEN rendering a Structured_Diff, THE Output_Renderer SHALL use the professional visual style defined for the interface and SHALL NOT expose raw tool-call JSON or literal escape sequences.

### Requirement 15: Cost and Token Budget Warnings

**User Story:** As a user, I want to be warned when a session's cost or token usage gets high, so that I can avoid unexpected spend.

#### Acceptance Criteria

1. WHILE a session is active, THE Omni_Dev_CLI SHALL track cumulative session cost and cumulative session token usage.
2. WHEN cumulative session cost exceeds the configured Cost_Threshold, THE Omni_Dev_CLI SHALL display a cost warning to the user.
3. WHEN cumulative session token usage exceeds the configured Token_Warning threshold, THE Omni_Dev_CLI SHALL display a token-usage warning to the user.
4. WHERE the user has acknowledged the Cost_Threshold warning, THE Omni_Dev_CLI SHALL persist the acknowledgement and SHALL NOT repeat the same cost warning in the current session after acknowledgement.
5. THE Omni_Dev_CLI SHALL continue to provide the existing cumulative cost and token summary on request in addition to threshold warnings.

### Requirement 16: Additional Utility Commands and First-Run Onboarding

**User Story:** As a user, I want the additional portable utility commands and a first-run onboarding flow, so that the Python CLI reaches parity with the reference tool's everyday conveniences.

#### Acceptance Criteria

1. WHEN the user invokes the bug command, THE Omni_Dev_CLI SHALL capture a bug report and store it locally.
2. WHEN the user invokes the pr_comments command, THE Omni_Dev_CLI SHALL fetch GitHub pull-request review comments using the available `gh` or `git` tooling and present a summary.
3. IF the pr_comments command cannot reach GitHub or the required tooling is unavailable, THEN THE Omni_Dev_CLI SHALL return a descriptive error without terminating the session.
4. WHEN the user invokes the release-notes command, THE Omni_Dev_CLI SHALL display the release notes or changelog.
5. WHEN the user invokes the terminalSetup command, THE Omni_Dev_CLI SHALL configure terminal keybindings or integration appropriate to the user's environment and SHALL persist the result in the Global_Config.
6. WHEN the user invokes the clear command, THE Omni_Dev_CLI SHALL reset the current conversation history.
7. WHEN the CLI starts in a project for which Project_Trust has not been accepted, THE Omni_Dev_CLI SHALL prompt the user with a first-run onboarding trust prompt before processing tasks.
8. WHEN the user accepts the onboarding trust prompt, THE Omni_Dev_CLI SHALL persist Project_Trust to the Project_Config so that the prompt is not shown again for that project.
9. WHEN the user invokes the help command, THE Omni_Dev_CLI SHALL list all available commands, including ported utility commands and any registered MCP-provided commands.
