"""
core.py - Enhanced OmniDevAgent

This is the central agent engine. It has been significantly enhanced by converting
features from the TypeScript scratch_repo:

NEW FEATURES (ported from scratch_repo):
  - All 15 tools now available (GlobTool, LSTool, NotebookTools, ArchitectTool added)
  - Concurrent tool execution for read-only tools (asyncio.gather)
  - Context injection: git status + directory structure + README in every prompt
  - AI-powered compact (summarize before clearing)
  - Cost tracking with per-call breakdown
  - FileEditTool uses old_string/new_string with single-match validation
  - Formal permission system

PRESERVED (unchanged from original):
  - Cognee memory integration (remember/recall via cognee.add/cognify/search)
  - litellm as LLM backend with model hot-swapping
  - Error handling and fallback without tools
  - Subagent spawning mechanism
"""

import os
import sys
import asyncio
import json
import uuid
import time
import warnings

import litellm
import cognee

# Suppress verbose logs
warnings.filterwarnings("ignore", category=UserWarning)

from src.tools import get_json_schemas, get_all_tools
from src.context import get_context, format_system_prompt_with_context, invalidate_context_cache
from src.cost_tracker import add_to_total_cost, get_tracker
from src.permissions import check_tool_permission


SYSTEM_INSTRUCTION = (
    "You are Omni-Dev, a highly capable autonomous coding agent with access to powerful tools. "
    "You can read/write files, run commands, search the codebase, manage memory, spawn sub-agents, "
    "browse the web, think through complex problems, list directories, find files with glob patterns, "
    "read/edit Jupyter notebooks, and generate architectural plans.\n\n"
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


def _clean_final_text(text: str) -> str:
    """
    Clean up the agent's final response text before showing it to the user.

    Some open-source models (e.g. Ollama/Gemma) wrap their last answer in a JSON
    tool-call block even when they just want to say "I'm done".  When that happens
    the raw JSON leaks into the UI.  This function:
    1. If the text is a bare think/summary JSON object, extracts the inner
       human-readable text from the 'thought' / 'summary' / 'content' field.
    2. Strips residual ``` fences around JSON blobs.
    3. Otherwise returns the text unchanged.
    """
    import json, re
    if not text:
        return text
    t = text.strip()
    # Case 1: entire response is a JSON object like {"name": "think", "arguments": {...}}
    if t.startswith("{") and "\"name\"" in t and "\"arguments\"" in t:
        try:
            obj = json.loads(t)
            args = obj.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            # Try common field names for human-readable content
            for field in ("thought", "summary", "content", "message", "text", "result"):
                if field in args:
                    return str(args[field]).strip()
            # Last resort: just return everything in args as readable text
            return "\n".join(f"**{k}:** {v}" for k, v in args.items())
        except Exception:
            pass
    # Case 2: response is a JSON array of tool calls
    if t.startswith("[") and "\"name\"" in t and "\"arguments\"" in t:
        try:
            items = json.loads(t)
            parts = []
            for item in items:
                args = item.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                for field in ("thought", "summary", "content", "message", "text", "result"):
                    if field in args:
                        parts.append(str(args[field]).strip())
                        break
            if parts:
                return "\n\n".join(parts)
        except Exception:
            pass
    # Case 3: response starts with a markdown ```json fence containing only a tool call
    m = re.match(r'^```(?:json)?\s*(\{.*?\})\s*```\s*$', t, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if "name" in obj and "arguments" in obj:
                args = obj["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                for field in ("thought", "summary", "content", "message", "text", "result"):
                    if field in args:
                        return str(args[field]).strip()
        except Exception:
            pass
    return text


class OmniDevAgent:
    """
    Main agentic loop.
    Enhanced with scratch_repo features while preserving all Cognee memory integration.
    """

    def __init__(self):
        self.model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        self.system_instruction = SYSTEM_INSTRUCTION
        self.messages = [{"role": "system", "content": self.system_instruction}]
        self._tool_instances = {tool.name: tool for tool in get_all_tools()}
        self._tool_schemas = get_json_schemas()
        self._context: dict = {}

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

    async def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Execute a single tool call."""
        tool = self._tool_instances.get(tool_name)
        if not tool:
            return f"Unknown tool: {tool_name}"

        # Check permissions
        perm = await check_tool_permission(tool_name, args)
        if not perm.allowed:
            return f"Permission denied: {perm.message}"

        try:
            result = await tool.call(**args)
            return str(result)
        except TypeError as e:
            # Handle missing/extra kwargs
            return f"Tool call error (bad arguments for {tool_name}): {e}"
        except Exception as e:
            return f"Tool error ({tool_name}): {e}"

    async def _execute_tools_concurrently(self, tool_calls: list) -> list:
        """
        Execute multiple read-only tool calls concurrently.
        Mirrors runToolsConcurrently from scratch_repo/src/query.ts.
        """
        tasks = []
        for tc in tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tasks.append(self._execute_tool(func_name, args))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            (tc, str(r) if not isinstance(r, Exception) else f"Tool error: {r}")
            for tc, r in zip(tool_calls, results)
        ]

    async def _execute_tools_serially(self, tool_calls: list, progress_callback=None) -> list:
        """
        Execute tool calls one at a time.
        Mirrors runToolsSerially from scratch_repo/src/query.ts.
        """
        results = []
        for tc in tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}

            if progress_callback:
                progress_callback(func_name, args)

            result = await self._execute_tool(func_name, args)
            results.append((tc, result))

        return results

    def _all_read_only(self, tool_calls: list) -> bool:
        """Check if all tool calls are read-only (can run concurrently)."""
        for tc in tool_calls:
            tool = self._tool_instances.get(tc.function.name)
            if tool is None or not tool.is_read_only():
                return False
        return True

    async def execute_task(self, prompt: str, progress_callback=None) -> str:
        """
        Main agentic loop.
        Mirrors query() from scratch_repo/src/query.ts.
        
        Enhancements:
        - Context injection (git status, directory structure)
        - Concurrent tool execution for read-only tools
        - Cost tracking per call
        """
        # Load and inject context into system prompt
        context = await self._load_context()
        full_system = format_system_prompt_with_context(self.system_instruction, context)

        # Update system message with latest context
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = full_system
        else:
            self.messages.insert(0, {"role": "system", "content": full_system})

        self.messages.append({"role": "user", "content": prompt})

        # Always use latest model in case user hot-swapped it
        model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro").strip()
        if model_name:
            if model_name.lower().startswith("model "):
                model_name = model_name[6:].strip()
            elif model_name.lower().startswith("models/"):
                model_name = model_name[7:].strip()
            elif model_name.lower().startswith("ollama "):
                model_name = "ollama/" + model_name[7:].strip()

            known_providers = ("groq/", "openai/", "anthropic/", "gemini/", "vertex_ai/", "openrouter/", "ollama/", "mistral/", "deepseek/", "huggingface/", "azure/", "cohere/")
            if not any(model_name.lower().startswith(p) for p in known_providers):
                lower_m = model_name.lower()
                if "/" in model_name:
                    model_name = "openrouter/" + model_name
                elif "oss" in lower_m or any(k in lower_m for k in ["llama", "mixtral", "gemma", "whisper"]):
                    model_name = "groq/" + model_name
                elif "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
                    model_name = "openai/" + model_name
                elif "claude" in lower_m:
                    model_name = "anthropic/" + model_name
                elif "gemini" in lower_m:
                    model_name = "gemini/" + model_name
                elif any(k in lower_m for k in ["glm", "qwen", "deepseek", "phi", "yi"]):
                    model_name = "openrouter/" + model_name

        completion_kwargs = {
            "model": model_name,
            "messages": self.messages,
            "tools": self._tool_schemas,
            "tool_choice": "auto",
        }
        if model_name.startswith("ollama/"):
            api_base = os.environ.get("OLLAMA_API_BASE")
            if not api_base and (os.environ.get("OLLAMA_API_KEY") or ":cloud" in model_name or "-cloud" in model_name or "cloud" in model_name.lower()):
                api_base = "https://ollama.com"
                os.environ["OLLAMA_API_BASE"] = api_base
            if api_base:
                completion_kwargs["api_base"] = api_base

        # Agentic loop (mirrors while(true) in query.ts)
        MAX_ITERATIONS = 40
        iteration = 0
        last_tool_signatures: set = set()
        while iteration < MAX_ITERATIONS:
            iteration += 1
            start_time = time.time()
            try:
                try:
                    response = litellm.completion(**completion_kwargs)
                except litellm.exceptions.BadRequestError:
                    # Fallback: try without tools (some models reject tool schemas)
                    fallback_kwargs = dict(completion_kwargs)
                    fallback_kwargs.pop("tools", None)
                    fallback_kwargs.pop("tool_choice", None)
                    response = litellm.completion(**fallback_kwargs)
                    text = response.choices[0].message.content or ""
                    return (
                        f"*(Warning: `{model_name}` rejected the tool schema. "
                        "Tools disabled. Switch to gpt-4o or gemini-1.5-pro for full capabilities.)*\n\n"
                        + text
                    )

            except Exception as e:
                error_msg = getattr(e, "message", str(e)) or repr(e)
                if "403" in error_msg or "Permission" in error_msg:
                    return f"🚨 **API Permission Error:** `{model_name}` access denied.\n\n*Raw Error:* {error_msg}"
                if any(k in error_msg.lower() for k in ["auth", "key", "401", "invalid_api_key"]):
                    return f"🚨 **API Key Missing/Invalid for `{model_name}`.**\nUse `/api_key` to set it.\n\n*Raw Error:* {error_msg}"
                return f"🚨 **LLM Error:** {error_msg}\n\n*Hint:* Use `/model` to switch providers."

            # Track cost
            duration_ms = round((time.time() - start_time) * 1000)
            if hasattr(response, "usage") and response.usage:
                u = response.usage
                add_to_total_cost(
                    model_name,
                    getattr(u, "prompt_tokens", 0),
                    getattr(u, "completion_tokens", 0),
                    duration_ms,
                )

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            # Fallback parser for open-source/cloud models that output tool calls as text inside content
            if not tool_calls and response_message.content:
                content_str = response_message.content.strip()
                # Only trigger fallback on EXPLICIT markers — not random text containing "name:"
                # This prevents the parser from firing on normal explanation text
                has_explicit_marker = (
                    "Tool Calls:" in content_str
                    or "tool_call" in content_str
                    or '```json' in content_str
                    or '```tool' in content_str
                    or ('"name"' in content_str and '"arguments"' in content_str)
                )
                if has_explicit_marker:
                    from types import SimpleNamespace
                    import re, uuid
                    valid_tools = set(self._tool_instances.keys())
                    
                    def repair_json_string(s: str) -> str:
                        def escape_match(match):
                            text = match.group(0)
                            if re.match(r'^\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})', text):
                                return text
                            return '\\\\' + text[1:]
                        s_repaired = re.sub(r'\\.', escape_match, s)
                        s_repaired = re.sub(r'\\$', r'\\\\', s_repaired)
                        return s_repaired

                    extracted = []
                    blocks = []
                    if "Tool Calls:" in content_str:
                        parts = content_str.split("Tool Calls:", 1)[1].strip()
                        idx = parts.find("[")
                        if idx != -1:
                            blocks.append(parts[idx:])
                    for match in re.finditer(r'```(?:json)?\s*([\[\{].*?[\]\}])\s*```', content_str, re.DOTALL):
                        blocks.append(match.group(1))

                    # Scan for all balanced {...} or [...] JSON blocks separated by <tool_call>, newlines, or whitespace
                    parts = re.split(r'<\|?/?tool_call\|?>|><tool_call>|```(?:json|tool)?|```', content_str)
                    for part in parts:
                        part = part.strip()
                        if not part:
                            continue
                        i = 0
                        while i < len(part):
                            if part[i] in '{[':
                                start = i
                                stack = [part[i]]
                                in_str = False
                                esc = False
                                i += 1
                                while i < len(part) and stack:
                                    c = part[i]
                                    if esc:
                                        esc = False
                                    elif c == '\\':
                                        esc = True
                                    elif c == '"':
                                        in_str = not in_str
                                    elif not in_str:
                                        if c == '{' or c == '[':
                                            stack.append(c)
                                        elif c == '}':
                                            if stack and stack[-1] == '{':
                                                stack.pop()
                                        elif c == ']':
                                            if stack and stack[-1] == '[':
                                                stack.pop()
                                    i += 1
                                if not stack:
                                    blocks.append(part[start:i])
                            else:
                                i += 1

                    for b in blocks:
                        try:
                            repaired_b = repair_json_string(b)
                            data = json.loads(repaired_b, strict=False)
                            items = data if isinstance(data, list) else [data]
                            for item in items:
                                if isinstance(item, dict):
                                    fn = item.get("function", item)
                                    name = fn.get("name")
                                    args = fn.get("arguments", {})
                                    if name in valid_tools:
                                        if isinstance(args, dict):
                                            args = json.dumps(args)
                                        call_id = item.get("id", f"call_{uuid.uuid4().hex[:8]}")
                                        extracted.append(SimpleNamespace(
                                            id=call_id,
                                            function=SimpleNamespace(name=name, arguments=args)
                                        ))
                        except Exception:
                            continue
                    if extracted:
                        # Deduplication: skip if we already ran the exact same tool calls last iteration
                        sig = tuple(sorted((tc.function.name, tc.function.arguments) for tc in extracted))
                        if sig in last_tool_signatures:
                            # Model is looping — treat as final response
                            final_text = content_str
                            self.messages.append({"role": "assistant", "content": final_text})
                            return _clean_final_text(final_text)
                        last_tool_signatures.add(sig)
                        tool_calls = extracted

            # Handle tool calls
            if tool_calls:
                # Store assistant's tool call message
                if response_message.tool_calls:
                    self.messages.append(response_message.model_dump())
                else:
                    tc_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                        }
                        for tc in tool_calls
                    ]
                    self.messages.append({"role": "assistant", "content": None, "tool_calls": tc_dicts})

                # Run read-only tools concurrently, others serially
                # Always announce tool calls via progress_callback (even concurrent ones)
                if progress_callback:
                    for tc in tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        progress_callback(tc.function.name, args)
                if self._all_read_only(tool_calls):
                    results = await self._execute_tools_concurrently(tool_calls)
                else:
                    results = await self._execute_tools_serially(tool_calls, progress_callback)

                # Add all tool results to message history
                for tc, result in results:
                    self.messages.append({
                        "role": "tool",
                        "name": tc.function.name,
                        "tool_call_id": tc.id,
                        "content": str(result),
                    })

            else:
                # No tool calls — we have a final response
                final_text = response_message.content or ""
                self.messages.append({"role": "assistant", "content": final_text})
                return _clean_final_text(final_text)

        # Exceeded max iterations — return whatever we have
        last_msg = self.messages[-1].get("content") or ""
        return _clean_final_text(last_msg) or "⚠️ Agent reached maximum iterations. Task may be incomplete."
