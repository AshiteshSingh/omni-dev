import os
import sys
import subprocess
import asyncio
import uuid
import requests
import json
import litellm
import cognee

# Define JSON Schema Tools for Litellm (Universal Format)
omni_tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a local file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a completely new file and write content to it. For existing files, you MUST use edit_file instead to save tokens.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                    "content": {"type": "string", "description": "The full text content to write."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgically edit an existing file by replacing a specific block of text. Use this instead of write_file for existing files to save tokens.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                    "target_content": {"type": "string", "description": "The exact block of text to be replaced. Must match exactly, including whitespace."},
                    "replacement_content": {"type": "string", "description": "The new block of text to insert in its place."}
                },
                "required": ["path", "target_content", "replacement_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command (e.g., npm run build, ls, pytest).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a fact, user preference, or project context into long-term Cognee graph memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact or context to remember."}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search long-term Cognee graph memory for past context or facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you want to search for."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Spawns a detached background sub-agent to work on a task independently. The sub-agent runs silently and uses 'remember' to save its final report to Cognee.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "A very detailed description of what the sub-agent needs to accomplish."}
                },
                "required": ["task_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the internet using SearXNG. Use this to find up-to-date information, documentation, or solutions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query string."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Use this tool to think out loud, reason through complex bugs, or architect a plan before taking action. Your thoughts will be saved to memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your detailed reasoning or architectural plan."}
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Search the local codebase for a text pattern or regex (similar to grep). Use this to quickly find where functions or variables are defined without reading whole files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The regex or text pattern to search for."},
                    "directory": {"type": "string", "description": "The directory to search in (use '.' for root)."}
                },
                "required": ["query", "directory"]
            }
        }
    }
]

class OmniDevAgent:
    def __init__(self):
        self.model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        
        self.system_instruction = (
            "You are Omni-Dev, a highly capable autonomous coding agent. "
            "You have access to tools to read files, write files, run terminal commands, and use Cognee for long-term memory. "
            "CRITICAL: NEVER guess file paths or URLs. You MUST use search_codebase or run_command('dir'/'ls') to verify a file exists before you attempt to edit or read it. "
            "If a tool returns an error, analyze the error and try a different approach."
        )
        self.messages = [{"role": "system", "content": self.system_instruction}]
        self.session_tokens = 0

    def get_token_usage(self):
        return self.session_tokens

    def compact_session(self):
        """Resets the short-term chat memory to save tokens while keeping long-term graph memory."""
        self.messages = [{"role": "system", "content": self.system_instruction}]

    # --- Tool Implementations ---
    def _tool_read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"

    def _tool_write_file(self, path: str, content: str) -> str:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def _tool_edit_file(self, path: str, target_content: str, replacement_content: str) -> str:
        try:
            if not os.path.exists(path):
                return f"Error: File {path} does not exist. Use write_file to create it."
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if target_content not in content:
                return "Error: target_content not found in file. Make sure your indentation and line breaks match exactly."
            new_content = content.replace(target_content, replacement_content, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
                
            await cognee.add(f"Agent edited file {path}. Replaced:\n{target_content}\nWith:\n{replacement_content}", dataset_name="agent_telemetry")
            await cognee.cognify()
            
            return f"Successfully edited {path} using smart chunk replacement."
        except Exception as e:
            return f"Error editing file: {e}"

    async def _tool_run_command(self, command: str) -> str:
        SAFE_COMMANDS = ["git status", "git diff", "git log", "git branch", "dir", "ls", "pwd", "tree", "date", "whoami", "npm run dev"]
        
        is_safe = False
        cmd_lower = command.lower().strip()
        for safe in SAFE_COMMANDS:
            if cmd_lower.startswith(safe):
                is_safe = True
                break
                
        if not is_safe:
            print(f"\n\033[91m[SECURITY WARNING] Omni-Dev wants to run a dangerous command:\033[0m {command}")
            user_approval = input("\033[93mAllow this command? (y/n): \033[0m").strip().lower()
            if user_approval != 'y':
                return "Command execution rejected by user."

        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout + "\n" + result.stderr
            
            await cognee.add(f"Agent ran terminal command: {command}\nOutput: {output}", dataset_name="agent_telemetry")
            await cognee.cognify()
            
            return output if output.strip() else "Command executed successfully with no output."
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error running command: {e}"

    async def _tool_remember(self, fact: str) -> str:
        try:
            await cognee.add(fact, dataset_name="user_memory")
            await cognee.cognify()
            return "Fact successfully saved to long-term memory."
        except Exception as e:
            return f"Error saving memory: {e}"

    async def _tool_recall(self, query: str) -> str:
        try:
            results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
            if results:
                return "\n".join(str(res) for res in results)
            return "No relevant memories found."
        except Exception as e:
            return f"Error recalling memory: {e}"

    def _tool_spawn_subagent(self, task_description: str) -> str:
        try:
            subagent_id = str(uuid.uuid4())[:8]
            if os.name == 'nt':
                CREATE_NO_WINDOW = 0x08000000
                subprocess.Popen(
                    [sys.executable, "subagent.py", task_description, subagent_id],
                    creationflags=CREATE_NO_WINDOW,
                    cwd=os.path.abspath(os.path.dirname(__file__))
                )
            else:
                subprocess.Popen(
                    [sys.executable, "subagent.py", task_description, subagent_id],
                    start_new_session=True,
                    cwd=os.path.abspath(os.path.dirname(__file__))
                )
            return f"Sub-agent '{subagent_id}' spawned successfully in the background. It will save its findings to memory when finished."
        except Exception as e:
            return f"Error spawning subagent: {e}"

    def _tool_search_web(self, query: str) -> str:
        try:
            searxng_url = os.environ.get("SEARXNG_URL", "https://searx.be")
            response = requests.get(
                f"{searxng_url}/search",
                params={"q": query, "format": "json"},
                timeout=15
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                if not results:
                    return "No results found."
                formatted = [f"Title: {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('content')}" for r in results[:5]]
                return "\n\n".join(formatted)
            else:
                return f"Search failed with status code {response.status_code}"
        except Exception as e:
            return f"Error searching the web: {e}"

    async def _tool_think(self, thought: str) -> str:
        await self._tool_remember(f"Thought Process: {thought}")
        return "Thought logged to memory successfully."

    def _tool_search_codebase(self, query: str, directory: str) -> str:
        try:
            import glob
            import re
            results = []
            for filepath in glob.glob(os.path.join(directory, '**', '*.*'), recursive=True):
                if 'node_modules' in filepath or '.git' in filepath or 'venv' in filepath:
                    continue
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if re.search(query, line):
                                results.append(f"{filepath}:{i+1}: {line.strip()}")
                except:
                    pass
            if results:
                return "\n".join(results[:50])
            return "No matches found."
        except Exception as e:
            return f"Error searching codebase: {e}"

    async def execute_task(self, prompt: str, progress_callback=None):
        self.messages.append({"role": "user", "content": prompt})
        
        # Always fetch latest model in case user hot-swapped it
        model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        
        while True:
            try:
                response = litellm.completion(
                    model=model_name,
                    messages=self.messages,
                    tools=omni_tools,
                    tool_choice="auto"
                )
            except Exception as e:
                error_msg = str(e)
                if "403" in error_msg or "Permission" in error_msg:
                    return f"🚨 **API Error:** Permission denied for `{model_name}`. Did you forget to configure your model? Try typing `/model ollama/llama3` or `/model groq/llama3-8192`.\n\n*Raw Error:* {error_msg[:150]}..."
                elif "API" in error_msg or "Auth" in error_msg or "key" in error_msg.lower():
                    return f"🚨 **API Key Missing:** The model `{model_name}` requires an API key. Please set it using `/api_key <PROVIDER> <key>` (e.g. `/api_key GROQ gsk_...`)."
                return f"🚨 **LLM Provider Error:** {error_msg}\nEnsure you have configured your model correctly using `/model <name>`."
                
            if hasattr(response, 'usage') and response.usage:
                self.session_tokens += getattr(response.usage, 'total_tokens', 0)
                
            response_message = response.choices[0].message
            
            if response_message.tool_calls:
                # Store assistant tool call request
                self.messages.append(response_message.model_dump())
                
                for tool_call in response_message.tool_calls:
                    func_name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except:
                        args = {}
                        
                    if progress_callback:
                        progress_callback(func_name, args)

                    # Execute the corresponding tool
                    if func_name == "read_file":
                        result = self._tool_read_file(**args)
                    elif func_name == "write_file":
                        result = self._tool_write_file(**args)
                    elif func_name == "edit_file":
                        result = await self._tool_edit_file(**args)
                    elif func_name == "run_command":
                        result = await self._tool_run_command(**args)
                    elif func_name == "remember":
                        result = await self._tool_remember(**args)
                    elif func_name == "recall":
                        result = await self._tool_recall(**args)
                    elif func_name == "spawn_subagent":
                        result = self._tool_spawn_subagent(**args)
                    elif func_name == "search_web":
                        result = self._tool_search_web(**args)
                    elif func_name == "think":
                        result = await self._tool_think(**args)
                    elif func_name == "search_codebase":
                        result = self._tool_search_codebase(**args)
                    else:
                        result = f"Unknown tool: {func_name}"

                    self.messages.append({
                        "role": "tool",
                        "name": func_name,
                        "tool_call_id": tool_call.id,
                        "content": str(result)
                    })
            else:
                final_text = response_message.content or ""
                self.messages.append({"role": "assistant", "content": final_text})
                return final_text
