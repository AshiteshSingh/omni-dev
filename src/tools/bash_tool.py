"""
BashTool - Python conversion of scratch_repo/src/tools/BashTool/BashTool.tsx

Enhanced version with:
- Banned command detection (mirrors TypeScript BANNED_COMMANDS)
- Safe command whitelist (mirrors permissions.ts SAFE_COMMANDS)
- User approval for dangerous commands
- Timeout support
- Output truncation (mirrors formatOutput utility)
"""
import os
import subprocess
import asyncio
from typing import Any, Dict, Optional

from .base_tool import BaseTool

# Commands that are completely banned (mirrors BashTool prompt.ts BANNED_COMMANDS)
BANNED_COMMANDS = [
    "rm", "rmdir", "del", "format", "mkfs", "dd", "sudo", "su",
    "chmod", "chown", "passwd", "shutdown", "reboot", "halt",
    "kill", "killall", "pkill", ":(){:|:&};:",  # fork bomb
    "curl | bash", "wget | bash", "eval", "exec",
]

# Commands that are known to be safe (mirrors permissions.ts SAFE_COMMANDS)
SAFE_COMMANDS = [
    "git status", "git diff", "git log", "git branch", "git show",
    "git fetch", "git remote",
    "pwd", "ls", "dir", "tree",
    "date", "which", "where",
    "cat", "head", "tail", "grep", "find", "echo",
    "python --version", "python3 --version", "node --version", "npm --version",
    "pip list", "pip show",
    "whoami", "hostname",
    "cd",
]

MAX_OUTPUT_LINES = 200
MAX_OUTPUT_CHARS = 50_000


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
    # Split on common shell operators
    parts = []
    current = ""
    for char in command:
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


class BashTool(BaseTool):
    """
    Execute shell/terminal commands.
    Python port of scratch_repo BashTool with security features.
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
            "Avoid running interactive commands that require stdin input."
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
                "description": "Optional timeout in seconds (max 600). Defaults to 120.",
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
        # 1. Check banned commands
        banned, reason = is_command_banned(command)
        if banned:
            return f"❌ Blocked: {reason}"

        # 2. Cap timeout
        timeout = min(timeout, 600)

        # 3. If not safe, request user approval
        if not is_command_safe(command) and os.environ.get("OMNI_AUTONOMOUS", "").lower() != "true":
            print(f"\n\033[91m[SECURITY] Agent wants to run:\033[0m {command}")
            try:
                approval = input("\033[93mAllow? (y/n): \033[0m").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "Command execution cancelled by user."
            if approval != "y":
                return "Command execution rejected by user."

        # 4. Execute
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.getcwd(),
            )
            stdout, _ = truncate_output(result.stdout or "")
            stderr, _ = truncate_output(result.stderr or "")

            output_parts = []
            if stdout.strip():
                output_parts.append(stdout.strip())
            if stderr.strip():
                output_parts.append(f"[stderr]\n{stderr.strip()}")
            if result.returncode != 0:
                output_parts.append(f"[Exit code: {result.returncode}]")

            return "\n".join(output_parts) if output_parts else "Command executed successfully with no output."

        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s."
        except Exception as e:
            return f"Error running command: {e}"
