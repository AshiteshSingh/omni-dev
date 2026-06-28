"""
BashTool - Python conversion of scratch_repo/src/tools/BashTool/BashTool.tsx

Enhanced version with:
- Banned command detection (mirrors TypeScript BANNED_COMMANDS)
- Safe command whitelist (mirrors permissions.ts SAFE_COMMANDS)
- User approval for dangerous commands
- Timeout support
- Output truncation (mirrors formatOutput utility)
- Windows-safe background process handling (& suffix converted to START)
"""
import os
import re
import subprocess
import asyncio
from typing import Any, Dict, Optional

from .base_tool import BaseTool
from .persistent_shell import get_shell

# Commands that are completely banned (mirrors BashTool prompt.ts BANNED_COMMANDS)
BANNED_COMMANDS = [
    "rm", "rmdir", "del", "format", "mkfs", "dd", "sudo", "su",
    "chmod", "chown", "passwd", "shutdown", "reboot", "halt",
    "killall", "pkill", ":{:|:&};:",  # fork bomb
    "curl | bash", "wget | bash", "eval", "exec",
]

# Commands that are known to be safe (mirrors permissions.ts SAFE_COMMANDS)
SAFE_COMMANDS = [
    "git status", "git diff", "git log", "git branch", "git show",
    "git fetch", "git remote",
    "pwd", "ls", "dir", "tree", "type",
    "date", "which", "where",
    "cat", "head", "tail", "grep", "find", "findstr", "echo",
    "python --version", "python3 --version", "node --version", "npm --version",
    "pip list", "pip show",
    "whoami", "hostname",
    "cd",
    "pytest", "npm test",
]

MAX_OUTPUT_LINES = 200
MAX_OUTPUT_CHARS = 50_000

# Seconds to capture early output from a background/server process before returning.
# 15s gives npx time to download packages before startup detection kicks in.
BACKGROUND_CAPTURE_SECS = 15

# Commands that are inherently long-running servers — auto-run in background
SERVER_COMMAND_PATTERNS = [
    # Python servers / frameworks
    r"python\S*\s+\S+\.py",        # python somefile.py / python3 app.py
    r"python\S*\s+-m\s+(http\.server|flask|uvicorn|gunicorn|aiohttp|tornado|sanic)",
    r"flask\s+run",
    r"uvicorn\s+",
    r"gunicorn\s+",
    r"fastapi\s+",
    r"django\S*\s+runserver",
    # Node / JS
    r"node\s+\S+",                  # node server.js
    r"npm\s+(start|run\s+dev|run\s+serve)",
    r"yarn\s+(start|dev|serve)",
    r"npx\s+(ts-node|tsx|nodemon|serve|http-server|live-server|lite-server|@angular/cli|react-scripts|vue-cli-service)",
    r"npx\s+exec\s+serve",
    r"npm\s+exec\s+serve",
    r"nodemon\s+",
    r"next\s+(start|dev)",
    r"vite\s*(--port)?",
    r"^serve\s+",                   # serve as standalone command (not as sub-command)
    # Ruby
    r"ruby\s+\S+\.rb",
    r"rails\s+server",
    # PHP
    r"php\s+-S\s+",
    # Go
    r"go\s+run\s+",
    # Java / Spring
    r"java\s+.*-jar",
    r"mvn\s+(spring-boot:run|tomcat:run)",
    # Networking / port forwarding
    r"http-server",
    r"live-server",
    r"netlify\s+(dev|serve)",
    r"vercel\s+dev",
]


def is_server_command(command: str) -> bool:
    """Detect if this command would launch a long-running blocking server process."""
    import re
    cmd = command.strip().rstrip("&").strip()
    for pattern in SERVER_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False


def is_command_safe(command: str) -> bool:
    """Check if command matches safe whitelist (prefix matching)."""
    cmd_lower = command.lower().strip()
    for safe in SAFE_COMMANDS:
        if cmd_lower.startswith(safe):
            return True
    return False


def is_command_banned(command: str) -> tuple[bool, str]:
    """
    Check if command contains a banned substring.
    Returns (is_banned, reason).
    Mirrors BashTool.validateInput from scratch_repo.
    """
    # Strip trailing & before checking
    clean = command.rstrip().rstrip("&").strip()
    parts = []
    current = ""
    for char in clean:
        if char in ("&", "|", ";"):
            if current.strip():
                parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())

    for part in parts:
        base_cmd = part.split()[0].lower() if part.split() else ""
        if base_cmd in BANNED_COMMANDS:
            return True, f"Command '{base_cmd}' is not allowed for security reasons."
    return False, ""


def truncate_output(text: str) -> tuple[str, int]:
    """
    Truncate output if it's too long.
    Returns (truncated_text, total_line_count).
    Mirrors formatOutput from BashTool/utils.ts.
    """
    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines > MAX_OUTPUT_LINES:
        truncated = "\n".join(lines[:MAX_OUTPUT_LINES])
        truncated += f"\n... (output truncated: {total_lines - MAX_OUTPUT_LINES} more lines. Use more specific commands to see less output.)"
        return truncated, total_lines
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n... (output truncated)", total_lines
    return text, total_lines


def is_background_command(command: str) -> bool:
    """Detect if the agent requested background execution (Unix & suffix)."""
    stripped = command.strip()
    # BUG FIX: must check endswith("&&") first — "&&" is NOT a background operator
    # Old code had: `stripped.endswith("&&") is False and stripped.endswith("&")` which
    # evaluated as `(stripped.endswith("&&") is False) and stripped.endswith("&")` —
    # that's always True when the string ends with any "&" including "&&".
    if not stripped.endswith("&&") and stripped.endswith("&"):
        return True
    # Also check for 'start ...' or 'nohup ...'  (Windows/Unix style)
    first_token = stripped.split()[0].lower() if stripped else ""
    if first_token in ("start", "nohup"):
        return True
    return False


def prepare_for_windows(command: str) -> tuple[str, bool]:
    """
    On Windows, convert Unix background execution patterns:
    - `command &` → run via `start /b cmd` and capture startup output only
    Returns (cleaned_command, is_background)
    """
    stripped = command.strip()
    is_bg = False

    # Strip trailing & (not &&)
    if re.search(r'(?<![&])&$', stripped):
        stripped = stripped[:-1].strip()
        is_bg = True

    # Strip 'nohup' prefix (not available on Windows)
    if stripped.lower().startswith("nohup "):
        stripped = stripped[6:].strip()
        is_bg = True

    return stripped, is_bg


class BashTool(BaseTool):
    """
    Execute shell/terminal commands.
    Python port of scratch_repo BashTool with security features.
    Windows-aware: handles background processes that would hang PowerShell.
    """

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command (e.g., git status, ls, pytest, npm run build). "
            "Use this to run tests, inspect files, install packages, or perform system tasks. "
            "Dangerous or destructive commands will require user approval. "
            "On Windows, background execution (& suffix) is handled automatically — "
            "the process is spawned in the background and early output is captured. "
            "Avoid running interactive commands that require stdin input. "
            "NOTE: This is a Windows PowerShell environment. Do NOT use Unix shell operators "
            "like sleep, &, nohup, or bash syntax. Use 'start' for background processes."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (max 600). Defaults to 120. For long-running commands, use a shorter timeout (e.g., 10) to check startup only.",
            },
        }

    @property
    def required_params(self):
        return ["command"]

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        command = input_args.get("command", "")
        return not is_command_safe(command)

    async def call(self, command: str, timeout: int = 120) -> str:
        """Execute the command with validation and security checks."""
        # 1. Normalize Windows background execution
        is_windows = os.name == "nt"
        clean_command = command
        is_bg = False

        # Auto-detect & strip trailing & on all platforms
        clean_command, is_bg = prepare_for_windows(command)

        # Also auto-detect long-running server commands — even without &
        if not is_bg and is_server_command(clean_command):
            is_bg = True

        # 2. Check banned commands
        banned, reason = is_command_banned(clean_command)
        if banned:
            return f"❌ Blocked: {reason}"

        # 3. Set effective timeout
        effective_timeout = min(timeout, 600)
        if is_bg:
            # Server/background: only wait BACKGROUND_CAPTURE_SECS for startup output
            effective_timeout = min(timeout, BACKGROUND_CAPTURE_SECS)

        # 4. If not safe, request user approval
        if not is_command_safe(clean_command) and os.environ.get("OMNI_AUTONOMOUS", "").lower() != "true":
            def _get_approval():
                from rich.console import Console
                from rich.prompt import Prompt
                con = Console(highlight=False)
                con.print(f"\n[bold red][SECURITY] Agent wants to run:[/bold red] [yellow]{command}[/yellow]")
                try:
                    return Prompt.ask(" [bold yellow]Allow? (y/n)[/bold yellow]").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return "n"

            approval = await asyncio.get_event_loop().run_in_executor(None, _get_approval)
            if approval != "y":
                return "Command execution rejected by user."

        # 5. Execute
        try:
            if is_bg:
                # Spawn as a detached background process
                kwargs = {
                    "shell": True,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "cwd": os.getcwd(),
                }
                if is_windows:
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    kwargs["start_new_session"] = True

                proc = subprocess.Popen(clean_command, **kwargs)

                # Give it a short window to produce startup output or crash.
                # We read stdout/stderr via threads so we don't deadlock on full pipe buffers.
                import threading
                _out_lines: list = []
                _err_lines: list = []

                def _reader(stream, buf):
                    try:
                        for line in iter(stream.readline, ""):
                            buf.append(line)
                    except Exception:
                        pass

                t_out = threading.Thread(target=_reader, args=(proc.stdout, _out_lines), daemon=True)
                t_err = threading.Thread(target=_reader, args=(proc.stderr, _err_lines), daemon=True)
                t_out.start()
                t_err.start()

                # Wait up to effective_timeout seconds for the process to exit (crash) or keep running
                try:
                    proc.wait(timeout=effective_timeout)
                    # Process exited quickly — wait for readers to finish
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    stdout = "".join(_out_lines)
                    stderr = "".join(_err_lines)
                except subprocess.TimeoutExpired:
                    # Process is still alive in background — startup succeeded
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    captured_out = "".join(_out_lines).strip()
                    captured_err = "".join(_err_lines).strip()
                    startup_output = ""
                    if captured_out:
                        startup_output += f"\nStartup stdout:\n{captured_out}"
                    if captured_err:
                        startup_output += f"\nStartup stderr:\n{captured_err}"
                    # Detect port from startup output
                    port_hint = ""
                    import re as _re
                    m = _re.search(r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)', captured_out + captured_err)
                    if m:
                        port_hint = f" Server is listening on port {m.group(1)}. Visit http://localhost:{m.group(1)}"
                    return (
                        f"✅ Background process started (PID {proc.pid}): `{clean_command}`\n"
                        f"Still running after {effective_timeout}s — startup succeeded.{port_hint}\n"
                        f"Do NOT run this command again — the server is already running.\n"
                        f"Use browser_action with action='goto' to open the URL directly."
                        + startup_output
                    )
                # Process exited quickly — return its output
                stdout_t, _ = truncate_output(stdout or "")
                stderr_t, _ = truncate_output(stderr or "")
                output_parts = []
                if stdout_t.strip():
                    output_parts.append(stdout_t.strip())
                if stderr_t.strip():
                    output_parts.append(f"[stderr]\n{stderr_t.strip()}")
                if proc.returncode != 0:
                    output_parts.append(f"[Exit code: {proc.returncode}] — Process exited immediately (startup error).")
                return "\n".join(output_parts) if output_parts else "Process exited immediately with no output."
            else:
                # Route normal (non-background) commands through the session
                # PersistentShell so cwd/env changes persist across invocations
                # (Requirements 11.1-11.4). Killing/respawning on timeout keeps
                # the shell usable for the next command (Requirement 11.5).
                shell = get_shell()
                loop = asyncio.get_event_loop()
                shell_result = await loop.run_in_executor(
                    None, lambda: shell.run(clean_command, timeout=effective_timeout)
                )

                if shell_result.timed_out:
                    note = f"Command timed out after {effective_timeout}s."
                    partial = (shell_result.stdout or "").strip()
                    if partial:
                        partial_t, _ = truncate_output(partial)
                        return f"{note}\n{partial_t.strip()}"
                    return note

                stdout, _ = truncate_output(shell_result.stdout or "")
                stderr, _ = truncate_output(shell_result.stderr or "")

                output_parts = []
                if stdout.strip():
                    output_parts.append(stdout.strip())
                if stderr.strip():
                    output_parts.append(f"[stderr]\n{stderr.strip()}")
                if shell_result.exit_code != 0:
                    output_parts.append(f"[Exit code: {shell_result.exit_code}]")

                return "\n".join(output_parts) if output_parts else "Command executed successfully with no output."

        except subprocess.TimeoutExpired:
            return f"Command timed out after {effective_timeout}s."
        except Exception as e:
            return f"Error running command: {e}"
