"""
core.py - Enhanced OmniDevAgent

This is the central agent engine. The agentic loop (``execute_task``) mirrors the
reference ``query.ts`` loop and is built on the consolidated service modules:

  - ``src.model_router``  : single authoritative model normalization + routing
                            (local vs cloud Ollama, ``ollama_chat/`` prefix, keys,
                            timeouts) and the injectable ``completion_fn``.
  - ``src.tool_policy``   : per-model decision on whether to send tool schemas
                            (replaces the old blanket ``disable_tools_for_model``).
  - ``src.agent.validation`` : JSON-schema + value-level validation of model args.
  - ``src.agent.tool_parser``: clean fallback parser for text tool-calls
                            (replaces the inline balanced-brace scanner,
                            ``repair_json_string`` and ``_clean_final_text``).

Loop behavior faithfully mirrors the reference:
  - schema validation before execution, value-level checks, unknown-tool handling,
  - bounded concurrency for read-only tools vs. serial for mutating tools,
  - ordered tool results, permission gating with autonomous bypass,
  - interrupt/cancellation via ``asyncio.Event``, head/tail result truncation.

PRESERVED:
  - Cognee memory integration (remember/recall via the memory tools)
  - Context injection (git status + directory structure + README)
  - Cost tracking, progress_callback announcements
"""

import os
import asyncio
import json
import inspect
import time
import warnings

# Suppress verbose logs
warnings.filterwarnings("ignore", category=UserWarning)

from src.tools import get_json_schemas, get_all_tools
from src.context import (
    get_context,
    format_system_prompt_with_context,
    invalidate_context_cache,
)
from src.cost_tracker import add_to_total_cost, get_tracker
from src import model_router
from src import tool_policy
from src.agent.validation import validate_tool_args, format_validation_error_content
from src.agent import tool_parser


SYSTEM_INSTRUCTION = (
    "You are Omni-Dev, a highly capable autonomous coding agent with access to powerful tools. "
    "You can read/write files, run commands, search the codebase, manage memory, spawn sub-agents, "
    "browse the web, think through complex problems, list directories, find files with glob patterns, "
    "read/edit Jupyter notebooks, and generate architectural plans.\n\n"
    "MEMORY RULES (CRITICAL — follow these to prevent AI amnesia):\n"
    "0. At the START of every new conversation or when context is unclear, call 'recall' with a broad "
    "   query like 'recent work sessions user projects portfolio' to load past context BEFORE doing anything.\n"
    "1. After completing any significant task (building a website, writing code, finishing a feature), "
    "   ALWAYS call 'remember' to store a concise summary: what was built, where the files are, what the user wanted.\n"
    "2. When the user says 'I told you before' or 'you forgot', immediately call 'recall' to retrieve it.\n"
    "3. ALWAYS use 'remember' after: creating files, building projects, learning user preferences, completing research.\n"
    "3b. After a burst of related 'remember' calls or at the end of a work session, call 'improve_memory' to "
    "   consolidate the Cognee graph (global context index + truth subspace) so future recall is sharper.\n"
    "4. CODEBASE / REPO MEMORY: When the user asks about a FILE, FOLDER, REPOSITORY, function, or CODE "
    "   (e.g. 'tell me about 36qubits.py', 'what does repo X do', 'explain the grover simulation'), you MUST "
    "   call 'recall' to search the Cognee graph memory with the file/topic name BEFORE touching the filesystem. "
    "   If the file is NOT found on disk, DO NOT give up and DO NOT ask the user where it is — it was very likely "
    "   cloned, indexed (via /index), and later deleted. The knowledge lives in graph memory, not on disk. "
    "   Call 'recall' (e.g. recall('36qubits.py grover simulation code')) and answer from what it returns. "
    "   Only fall back to filesystem tools (list_dir/find/grep) if recall returns nothing useful.\n\n"
    "ENVIRONMENT: Windows 11 — PowerShell shell. NEVER use Unix shell syntax:\n"
    "  ❌ WRONG (Linux/Mac): python server.py &   sleep 2 && curl   nohup python app.py\n"
    "  ✅ CORRECT (Windows): python server.py  (with a short timeout)  or  start python server.py\n"
    "  ❌ WRONG: command1 && command2  — on some models this may fail; use separate run_command calls\n"
    "  ✅ For starting background servers: use run_command with a short timeout (e.g., 5s) so the process "
    "is spawned and you can continue without blocking. The tool will automatically detect & and handle it.\n\n"
    "CRITICAL RULES:\n"
    "1. NEVER guess file paths. Use list_dir or glob_files to verify paths first.\n"
    "2. ALWAYS read a file before editing it (so you have the exact content).\n"
    "3. When editing, use old_string/new_string with enough context to be unique.\n"
    "4. Use think to reason through complex problems before coding.\n"
    "5. Use remember to save important findings for future sessions.\n"
    "6. Use spawn_subagent for long background tasks.\n"
    "7. Analyze errors and try alternative approaches instead of giving up.\n"
    "8. Use architect to plan large features before implementing them.\n"
    "9. Use browser_action to control a real web browser (open URL, click, type, scroll, screenshot like a human).\n"
    "10. Use read_url_content to quickly fetch and read full text from documentation or search result URLs.\n"
    "11. Use ask_user whenever you need user clarification or feedback mid-task. Do NOT stop your turn to ask a question; call ask_user instead.\n"
    "12. NEVER run long-running blocking commands without a short timeout. Server start commands must use timeout=5 or less."
)


# Loop bounds — mirror the reference query.ts constants.
MAX_ITERATIONS = 40
MAX_TOOL_USE_CONCURRENCY = 10
MAX_TOOL_RESULT_CHARS = 10_000

# Prompt-time GraphRAG injection bounds (keep the injected block concise).
MAX_GRAPH_CONTEXT_EDGES = 40


def _graph_node_label(node) -> str:
    """Render a concise ``type name (path)`` label for a graph node."""
    attrs = getattr(node, "attrs", None) or {}
    name = attrs.get("name") or attrs.get("path") or attrs.get("summary")
    if not name:
        name = getattr(node, "id", "?")
    path = attrs.get("path")
    ntype = getattr(node, "type", "node")
    if path and name != path:
        return f"{ntype} {name} ({path})"
    return f"{ntype} {name}"


def _render_subgraph_text(result) -> str:
    """Render a retrieved subgraph as a concise text summary for prompt context.

    Lists the matched/related nodes (type, name, path) and their key
    relationships, prefixed by any staleness notice carried on the result.
    """
    lines: list[str] = []
    notice = getattr(result, "notice", None)
    if notice:
        lines.append(str(notice))

    lines.append("Relevant code entities (from the codebase knowledge graph):")
    for node in result.nodes:
        lines.append(f"- {_graph_node_label(node)}")

    edges = getattr(result, "edges", None) or []
    if edges:
        nodes_by_id = {n.id: n for n in result.nodes}

        def _short(node_id):
            node = nodes_by_id.get(node_id)
            if node is None:
                return node_id
            attrs = getattr(node, "attrs", None) or {}
            return attrs.get("name") or attrs.get("path") or node_id

        lines.append("Relationships:")
        for edge in edges[:MAX_GRAPH_CONTEXT_EDGES]:
            lines.append(f"- {_short(edge.src)} --{edge.type}--> {_short(edge.dst)}")

    return "\n".join(lines)


class OmniDevAgent:
    """
    Main agentic loop. Mirrors query() from scratch_repo/src/query.ts using the
    consolidated router / policy / validation / parser modules.
    """

    def __init__(self):
        # Clean up process environment variables to prevent malformed values/whitespaces
        for key in list(os.environ.keys()):
            if key.endswith("_API_KEY") or key.endswith("_API_BASE") or key == "OMNI_MODEL":
                val = os.environ[key].strip()
                if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                    val = val[1:-1].strip()
                if not val:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

        self.model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        self.system_instruction = SYSTEM_INSTRUCTION
        self.messages = [{"role": "system", "content": self.system_instruction}]
        self._tool_instances = {tool.name: tool for tool in get_all_tools()}
        self._tool_schemas = get_json_schemas()
        self._context: dict = {}

        # Autonomous mode bypasses the Permission_Check (Req 7.10). May also be
        # toggled via the OMNI_AUTONOMOUS environment variable.
        self.autonomous = False
        # Injected permission callback (the reference ``canUseTool``). The
        # interface layer sets this to an interactive approve/deny/remember
        # prompt. When None, the loop falls back to the permissions module.
        self.can_use_tool = None
        # Optional async streaming renderer injected by the interface:
        #   async def stream_render(stream) -> (raw_text, tool_calls)
        # When set, the loop streams the model response token-by-token through it
        # for live rendering. When None, the loop uses a single non-streaming call.
        self.stream_render = None

    # ------------------------------------------------------------------ #
    # Session helpers
    # ------------------------------------------------------------------ #

    def get_token_usage(self) -> int:
        return get_tracker().total_tokens

    def get_cost_summary(self) -> str:
        return get_tracker().get_summary()

    def compact_session(self):
        """Simple reset (use AI compact via compact_command for smart version)."""
        self.messages = [{"role": "system", "content": self.system_instruction}]
        self._context = {}
        invalidate_context_cache()

    async def _load_context(self) -> dict:
        """Load and cache context (git status, directory structure, README)."""
        if not self._context:
            try:
                self._context = await get_context()
            except Exception:
                self._context = {}
        return self._context

    # ------------------------------------------------------------------ #
    # Autonomous mode / interrupt helpers
    # ------------------------------------------------------------------ #

    def _is_autonomous(self) -> bool:
        """True when Autonomous_Mode is enabled (attribute or OMNI_AUTONOMOUS)."""
        if getattr(self, "autonomous", False):
            return True
        val = os.environ.get("OMNI_AUTONOMOUS", "").strip().lower()
        return val in ("1", "true", "yes", "on")

    @staticmethod
    def _aborted(abort_event) -> bool:
        """True when an interrupt has been requested via the abort event."""
        try:
            return abort_event is not None and abort_event.is_set()
        except Exception:
            return False

    def _interrupt_return(self) -> str:
        """Append a benign assistant message so history stays consistent (Req 7.12)."""
        message = (
            "⚠️ **Interrupted.** The task was stopped before completion. "
            "You can issue a new request to continue."
        )
        # Only append if the last message isn't already a dangling tool-call
        # assistant message; the loop guarantees we never reach here with one.
        self.messages.append({"role": "assistant", "content": message})
        return message

    @staticmethod
    def _truncate_result(content) -> str:
        """Head/tail truncate oversized tool result content (Req 7.13)."""
        if content is None:
            return ""
        content = str(content)
        if len(content) <= MAX_TOOL_RESULT_CHARS:
            return content
        head_len = MAX_TOOL_RESULT_CHARS // 2
        tail_len = MAX_TOOL_RESULT_CHARS - head_len
        omitted = len(content) - (head_len + tail_len)
        return (
            content[:head_len]
            + f"\n\n... [{omitted} characters truncated] ...\n\n"
            + content[len(content) - tail_len:]
        )

    # ------------------------------------------------------------------ #
    # Permission gating
    # ------------------------------------------------------------------ #

    @staticmethod
    def _interpret_permission(result):
        """Normalize a permission-check return value into ``(allowed, reason)``."""
        if result is None:
            return True, ""
        if isinstance(result, bool):
            return result, "" if result else "denied by permission check"
        if isinstance(result, (tuple, list)) and result:
            allowed = bool(result[0])
            reason = str(result[1]) if len(result) >= 2 and result[1] else ""
            return allowed, reason or ("" if allowed else "denied by permission check")
        if isinstance(result, dict):
            allowed = bool(result.get("allowed", result.get("ok", False)))
            reason = str(result.get("message", result.get("reason", "")) or "")
            return allowed, reason or ("" if allowed else "denied by permission check")
        # Duck-typed PermissionResult-like object.
        allowed_attr = getattr(result, "allowed", getattr(result, "ok", None))
        if allowed_attr is not None:
            reason = getattr(result, "message", "") or getattr(result, "reason", "") or ""
            allowed = bool(allowed_attr)
            return allowed, str(reason) or ("" if allowed else "denied by permission check")
        return bool(result), "" if result else "denied by permission check"

    async def _check_permission(self, tool, tool_name: str, args: dict):
        """Run the Permission_Check, preferring the injected ``can_use_tool``.

        Returns ``(allowed, reason)``. Tolerant of differing callback/module
        signatures so it keeps working with the current permissions module
        (which exposes ``check_tool_permission``) and any future rebuild.
        """
        callback = getattr(self, "can_use_tool", None)
        if callback is not None:
            try:
                # Try the richest signature first, then degrade gracefully.
                try:
                    result = callback(tool, args)
                except TypeError:
                    try:
                        result = callback(tool_name, args)
                    except TypeError:
                        result = callback(tool_name)
                if inspect.isawaitable(result):
                    result = await result
                return self._interpret_permission(result)
            except Exception as exc:
                return False, f"permission check failed: {exc}"

        # Fall back to the permissions module (currently exposes
        # check_tool_permission). Import defensively and tolerate signature drift.
        try:
            from src import permissions as _permissions
        except Exception:
            return True, ""

        check = getattr(_permissions, "has_permission", None) or getattr(
            _permissions, "check_tool_permission", None
        )
        if check is None:
            return True, ""

        try:
            try:
                result = check(tool_name, args)
            except TypeError:
                try:
                    result = check(tool, args)
                except TypeError:
                    result = check(tool_name)
            if inspect.isawaitable(result):
                result = await result
            return self._interpret_permission(result)
        except Exception as exc:
            return False, f"permission check failed: {exc}"

    # ------------------------------------------------------------------ #
    # Tool execution
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_args(tc) -> dict:
        raw = tc.function.arguments
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def _process_tool_call(self, tc, index: int):
        """Validate, gate, and execute a single tool call.

        Returns ``(index, tc, content, is_error)``. Never raises: tool errors are
        captured and returned as flagged error results so the loop continues
        (Req 6.3, 7.9).
        """
        tool_name = tc.function.name
        args = self._parse_args(tc)

        # Unknown tool (Req 7.4).
        tool = self._tool_instances.get(tool_name)
        if tool is None:
            return index, tc, f"Error: No such tool available: {tool_name}", True

        # Schema + value-level validation BEFORE execution (Req 7.1, 7.2, 7.3).
        validation = validate_tool_args(tool, args)
        if not validation.ok:
            return index, tc, format_validation_error_content(validation), True

        # Permission gating unless Autonomous_Mode (Req 7.8, 7.9, 7.10).
        if not self._is_autonomous():
            allowed, reason = await self._check_permission(tool, tool_name, args)
            if not allowed:
                detail = reason or "denied"
                return index, tc, f"Permission denied: {detail}", True

        # Execute, capturing any error (Req 6.3, 7.9).
        try:
            result = await tool.call(**args)
            return index, tc, str(result), False
        except TypeError as exc:
            return index, tc, f"Tool call error (bad arguments for {tool_name}): {exc}", True
        except Exception as exc:
            return index, tc, f"Tool error ({tool_name}): {exc}", True

    def _all_read_only(self, tool_calls) -> bool:
        """True only if every requested tool is a known Read_Only_Tool (Req 7.5)."""
        for tc in tool_calls:
            tool = self._tool_instances.get(tc.function.name)
            if tool is None or not tool.is_read_only():
                return False
        return True

    async def _execute_tools_concurrently(self, tool_calls) -> list:
        """Run read-only tool calls concurrently with a bounded semaphore (Req 7.6)."""
        semaphore = asyncio.Semaphore(MAX_TOOL_USE_CONCURRENCY)

        async def run_one(i, tc):
            async with semaphore:
                return await self._process_tool_call(tc, i)

        results = await asyncio.gather(
            *(run_one(i, tc) for i, tc in enumerate(tool_calls))
        )
        return list(results)

    async def _execute_tools_serially(self, tool_calls) -> list:
        """Run tool calls one at a time, in order."""
        results = []
        for i, tc in enumerate(tool_calls):
            results.append(await self._process_tool_call(tc, i))
        return results

    # ------------------------------------------------------------------ #
    # Model invocation
    # ------------------------------------------------------------------ #

    def _build_kwargs(self, decision, include_tools: bool) -> dict:
        kwargs = {
            "model": decision.canonical_model,
            "messages": self.messages,
            "timeout": decision.timeout,
        }
        if include_tools:
            kwargs["tools"] = self._tool_schemas
            kwargs["tool_choice"] = "auto"
        if decision.api_base:
            kwargs["api_base"] = decision.api_base
        if decision.api_key:
            kwargs["api_key"] = decision.api_key
        return kwargs

    @staticmethod
    def _is_tool_rejection(exc) -> bool:
        """Heuristic: did a request fail because tool schemas were rejected? (Req 2.4)"""
        text = str(getattr(exc, "message", "") or str(exc)).lower()
        name = type(exc).__name__.lower()
        if "badrequest" in name or "400" in name:
            return True
        return any(
            k in text
            for k in ("400", "bad request", "tool", "function", "ollamaexception")
        )

    @staticmethod
    def _classify_error(exc, model_name: str) -> str:
        """Map a request exception to a descriptive user-facing error (Req 6.1, 6.2)."""
        error_msg = getattr(exc, "message", "") or str(exc) or repr(exc)
        low = error_msg.lower()
        if any(k in error_msg for k in ["10061", "Connection refused", "actively refused", "Failed to connect"]):
            return (
                f"🚨 **Ollama / Local Server Offline (`{model_name}`):** Could not connect to the local API server.\n\n"
                "*Fix:* If using Ollama, open a new terminal and run `ollama serve`. Or type `/model` to switch to a "
                "cloud provider (e.g., `groq/llama-3.3-70b-versatile`, `gemini/gemini-2.5-pro`, or `openai/gpt-4o`)."
            )
        if "403" in error_msg or "permission" in low or "access denied" in low:
            return f"🚨 **API Permission Error:** `{model_name}` access denied.\n\n*Raw Error:* {error_msg}"
        if any(k in low for k in ["auth", "api key", "api_key", "401", "invalid_api_key", "unauthorized"]):
            return f"🚨 **API Key Missing/Invalid for `{model_name}`.**\nUse `/api_key` to set it.\n\n*Raw Error:* {error_msg}"
        if "tool" in low or "function" in low:
            return (
                f"🚨 **Tool Schema Error:** Model `{model_name}` had an issue with tool definitions.\n\n"
                f"This model may not support function calling. Try a different model with `/model`.\n\n*Raw Error:* {error_msg}"
            )
        return f"🚨 **LLM Error:** {error_msg}\n\n*Hint:* Use `/model` to switch providers."

    # ------------------------------------------------------------------ #
    # The agentic loop
    # ------------------------------------------------------------------ #

    async def execute_task(self, prompt, progress_callback=None, abort_event=None) -> str:
        """
        Main agentic loop. Mirrors query() from scratch_repo/src/query.ts.

        Routes through the Model Router + Tool Capability Policy, calls the model
        via the injectable ``completion_fn``, validates/gates/executes tool calls
        (concurrent for read-only, serial otherwise), orders and truncates the
        results, supports interrupt and the text-tool-call fallback, and iterates
        until a Final_Response or the iteration bound.
        """
        # --- Context injection (preserved) ---
        self._final_was_streamed = False
        context = await self._load_context()

        # --- Best-effort prompt-time GraphRAG context injection (Req 4.6, 9.4) ---
        # Local-only: load the knowledge graph and, if non-empty, retrieve a
        # relevant subgraph for the prompt and inject it as a new ``codebaseGraph``
        # context key. The ENTIRE block is wrapped so ANY failure (missing graph,
        # import error, retrieval error) leaves the existing static-context
        # behavior completely unchanged and never raises into the loop. Imports
        # are lazy/local to avoid import-time coupling.
        try:
            from src.graph.store import GraphStore
            from src.graph.config import get_graph_config
            from src.graph.retrieval import GraphRAGRetriever

            graph, meta = GraphStore().load()
            if graph.nodes:
                retriever = GraphRAGRetriever(
                    graph, get_graph_config(), os.getcwd(), meta
                )
                result = retriever.retrieve(prompt)
                if result.nodes:
                    context = {**context, "codebaseGraph": _render_subgraph_text(result)}
        except Exception:
            # Graph context is strictly additive and best-effort; never block the prompt.
            pass

        full_system = format_system_prompt_with_context(self.system_instruction, context)
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = full_system
        else:
            self.messages.insert(0, {"role": "system", "content": full_system})

        self.messages.append({"role": "user", "content": prompt})

        # --- Routing via the Model Router (Req 5.2) ---
        raw_model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        decision = model_router.route(raw_model, os.environ)

        # Routing error (e.g. cloud Ollama without a key) — return WITHOUT calling
        # the backend (Req 1.5).
        if decision.error:
            return f"🚨 **Configuration Error:** {decision.error}"

        model_name = decision.canonical_model

        # Capability policy decides whether to attach tool schemas (Req 2.1, 2.2).
        tools_enabled = tool_policy.supports_tools(decision)

        # Local Ollama connectivity: probe + single ``ollama serve`` retry (Req 1.6).
        if decision.is_ollama and not decision.is_cloud_ollama:
            conn_error = model_router.ensure_local_ollama(decision.api_base)
            if conn_error:
                return f"🚨 **Ollama Connectivity Error:** {conn_error}"

        completion_fn = model_router.get_completion_fn()
        valid_tools = set(self._tool_instances.keys())

        iteration = 0
        last_tool_signatures: set = set()
        tools_currently_enabled = tools_enabled
        retried_without_tools = False

        while iteration < MAX_ITERATIONS:
            iteration += 1

            # Interrupt before issuing the model request (Req 7.11, 7.12).
            if self._aborted(abort_event):
                return self._interrupt_return()

            start_time = time.time()

            # --- Model call: stream when a renderer is injected, else one-shot ---
            streamed = False
            content = ""
            tool_calls = None
            usage = None
            response = None

            stream_render = getattr(self, "stream_render", None)
            if stream_render is not None:
                try:
                    kwargs = self._build_kwargs(decision, tools_currently_enabled)
                    kwargs["stream"] = True
                    try:
                        kwargs["stream_options"] = {"include_usage": True}
                    except Exception:
                        pass
                    stream = completion_fn(**kwargs)

                    # Tap each chunk for usage (cost tracking) while the renderer
                    # consumes the stream for live display.
                    captured = {"usage": None}

                    def _tap(src):
                        for ch in src:
                            try:
                                u = getattr(ch, "usage", None)
                                if u:
                                    captured["usage"] = u
                            except Exception:
                                pass
                            yield ch

                    content, tool_calls = await stream_render(_tap(stream))
                    usage = captured["usage"]
                    streamed = True
                except Exception:
                    # Streaming failed to even start — fall back to non-streaming.
                    streamed = False

            # An empty stream (e.g. auth error surfaced mid-iteration) falls back
            # so the non-streaming path can produce a real response / clear error.
            if streamed and not (content and content.strip()) and not tool_calls:
                streamed = False

            if not streamed:
                # --- Non-streaming call with retry-once-without-tools (Req 2.4) ---
                try:
                    try:
                        response = completion_fn(**self._build_kwargs(decision, tools_currently_enabled))
                    except Exception as exc:
                        if (
                            tools_currently_enabled
                            and not retried_without_tools
                            and self._is_tool_rejection(exc)
                        ):
                            tools_currently_enabled = False
                            retried_without_tools = True
                            response = completion_fn(**self._build_kwargs(decision, False))
                        else:
                            raise
                except Exception as exc:
                    return self._classify_error(exc, model_name)

                usage = getattr(response, "usage", None)
                response_message = response.choices[0].message
                content = response_message.content or ""
                tool_calls = response_message.tool_calls

            # --- Cost tracking (preserved; works for both paths) ---
            duration_ms = round((time.time() - start_time) * 1000)
            if usage:
                add_to_total_cost(
                    model_name,
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                    duration_ms,
                )

            used_text_parser = False

            # --- Text tool-call fallback (Req 3.5) ---
            # Only when there are no native tool calls but the content carries
            # explicit tool-call structures.
            if not tool_calls and content:
                parsed = tool_parser.extract_tool_calls(content, valid_tools)
                if parsed:
                    # Repeated identical text tool-calls -> stop, return final (Req 6.5).
                    signature = tuple(
                        sorted((c.function.name, c.function.arguments) for c in parsed)
                    )
                    if signature in last_tool_signatures:
                        final_text = tool_parser.strip_tool_call_text(content)
                        self.messages.append({"role": "assistant", "content": content})
                        return final_text or self._empty_response_notice(model_name)
                    last_tool_signatures.add(signature)
                    tool_calls = parsed
                    used_text_parser = True

            # --- No tool calls: Final_Response (Req 2.7) ---
            if not tool_calls:
                self.messages.append({"role": "assistant", "content": content})
                final_text = tool_parser.strip_tool_call_text(content)
                if not final_text.strip():
                    return self._empty_response_notice(model_name)
                # Tell the interface whether the final answer was already shown
                # live by the stream renderer (so it doesn't double-render).
                self._final_was_streamed = bool(streamed)
                return final_text

            # --- We have tool calls: record the assistant message ---
            assistant_msg = {
                "role": "assistant",
                "content": content if content else None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
            self.messages.append(assistant_msg)

            # --- Progress announcements (preserved) ---
            if progress_callback:
                # When streamed, the assistant prose was already rendered live by
                # the stream renderer — don't re-announce it.
                if content.strip() and not streamed:
                    announce = tool_parser.strip_tool_call_text(content)
                    if announce.strip():
                        progress_callback("assistant_message", {"content": announce})
                for tc in tool_calls:
                    progress_callback(tc.function.name, self._parse_args(tc))

            # Interrupt before executing the tool round (Req 7.11). The assistant
            # tool-call message is already recorded; append a benign tool result
            # for each pending call so history stays consistent (Req 7.12).
            if self._aborted(abort_event):
                for tc in tool_calls:
                    self.messages.append({
                        "role": "tool",
                        "name": tc.function.name,
                        "tool_call_id": tc.id,
                        "content": "Interrupted: tool execution was cancelled by the user.",
                    })
                return self._interrupt_return()

            # --- Execute: concurrent for all-read-only, else serial (Req 7.5, 7.6) ---
            if self._all_read_only(tool_calls):
                results = await self._execute_tools_concurrently(tool_calls)
            else:
                results = await self._execute_tools_serially(tool_calls)

            # --- Ordered tool results, truncated, appended in call order (Req 7.7, 7.13) ---
            for index, tc, result_content, is_error in sorted(results, key=lambda r: r[0]):
                self.messages.append({
                    "role": "tool",
                    "name": tc.function.name,
                    "tool_call_id": tc.id,
                    "content": self._truncate_result(result_content),
                })
                # Surface a concise completion line for the UI (Claude-Code-style
                # "result" feedback under each activity). Best-effort; never breaks
                # the loop if the callback raises.
                if progress_callback:
                    try:
                        progress_callback("__tool_result__", {
                            "tool": tc.function.name,
                            "args": self._parse_args(tc),
                            "result": str(result_content),
                            "is_error": bool(is_error),
                        })
                    except Exception:
                        pass

        # Reached MAX_ITERATIONS with pending tool calls -> incompleteness (Req 2.8).
        return (
            f"⚠️ **Task may be incomplete.** The agent reached the maximum of {MAX_ITERATIONS} "
            "iterations without producing a final answer. Try narrowing the request, or continue "
            "with a follow-up instruction."
        )

    @staticmethod
    def _empty_response_notice(model_name: str) -> str:
        """Descriptive notice + recovery suggestion for an empty Final_Response (Req 6.4)."""
        return (
            f"🚨 **Empty response from `{model_name}`.** The model returned no usable content.\n\n"
            "*Try:* Rephrase your request, or use `/model` to switch to a cloud provider such as "
            "`groq/llama-3.3-70b-versatile` or `gemini/gemini-2.5-pro`."
        )
