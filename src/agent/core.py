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
    "CRITICAL RULES:\n"
    "1. NEVER guess file paths. Use list_dir or glob_files to verify paths first.\n"
    "2. ALWAYS read a file before editing it (so you have the exact content).\n"
    "3. When editing, use old_string/new_string with enough context to be unique.\n"
    "4. Use think to reason through complex problems before coding.\n"
    "5. Use remember to save important findings for future sessions.\n"
    "6. Use spawn_subagent for long background tasks.\n"
    "7. Analyze errors and try alternative approaches instead of giving up.\n"
    "8. Use architect to plan large features before implementing them.\n"
    "9. Use browser_action to control a real web browser (open URL, click, type, scroll, screenshot like a human)."
)


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
        if model_name and "/" not in model_name:
            lower_m = model_name.lower()
            if any(k in lower_m for k in ["llama", "mixtral", "gemma", "deepseek", "whisper"]):
                model_name = "groq/" + model_name
            elif "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
                model_name = "openai/" + model_name
            elif "claude" in lower_m:
                model_name = "anthropic/" + model_name
            elif "gemini" in lower_m:
                model_name = "gemini/" + model_name

        # Agentic loop (mirrors while(true) in query.ts)
        while True:
            start_time = time.time()
            try:
                try:
                    response = litellm.completion(
                        model=model_name,
                        messages=self.messages,
                        tools=self._tool_schemas,
                        tool_choice="auto",
                    )
                except litellm.exceptions.BadRequestError:
                    # Fallback: try without tools (some models reject tool schemas)
                    response = litellm.completion(
                        model=model_name,
                        messages=self.messages,
                    )
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

            # Handle tool calls
            if response_message.tool_calls:
                # Store assistant's tool call message
                self.messages.append(response_message.model_dump())

                tool_calls = response_message.tool_calls

                # Run read-only tools concurrently, others serially
                # Mirrors query.ts runToolsConcurrently / runToolsSerially
                if self._all_read_only(tool_calls):
                    # Announce all tools at once (for concurrent mode)
                    if progress_callback:
                        for tc in tool_calls:
                            try:
                                args = json.loads(tc.function.arguments)
                            except Exception:
                                args = {}
                            progress_callback(tc.function.name, args)
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
                return final_text
