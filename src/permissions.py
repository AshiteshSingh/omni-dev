"""
permissions.py - Granular persistent tool-permission system.

Ported from ``scratch_repo/src/permissions.ts`` (and ``utils/commands.ts``) to
Python. Replaces the old all-or-nothing ``check_tool_permission`` with the
reference's granular model:

- ``SAFE_COMMANDS`` allowlist that never needs approval (Req 10.2).
- Bash permission keys of the form ``run_command(<prefix>:*)`` (Req 10.3, 10.4).
- Command-injection detection (``;``, ``&&``, ``||``, ``|``, ``$(``, backticks)
  that forces exact-match approval (Req 10.5).
- File-edit tools grant a session-only write permission rather than persisting
  a permission entry to the Project_Config (Req 10.6, 10.7).
- Autonomous_Mode bypass (Req 10.8) and blanket ``run_command`` grant (Req 10.10).

A backward-compatible ``check_tool_permission`` shim is kept because the agent
loop calls into this module defensively (``has_permission`` / ``check_tool_permission``).
"""
from __future__ import annotations

import os
import shlex
from collections import namedtuple
from typing import Any, Dict, Optional, Union

from . import config_store

# ---------------------------------------------------------------------------
# Safe commands (Req 10.2) - mirrors permissions.ts SAFE_COMMANDS exactly
# ---------------------------------------------------------------------------

SAFE_COMMANDS = {
    "git status",
    "git diff",
    "git log",
    "git branch",
    "pwd",
    "tree",
    "date",
    "which",
}

# ---------------------------------------------------------------------------
# Tool-name groups (this project's tool names + reference aliases)
# ---------------------------------------------------------------------------

#: The bash / shell tool in this project.
BASH_TOOL_NAME = "run_command"

#: File-editing tools that use a session-only write grant rather than a
#: persisted permission entry (Req 10.7). Both this project's names and the
#: reference names are accepted so callers can pass either.
FILE_EDIT_TOOLS = {
    "write_file", "file_write",       # FileWriteTool
    "edit_file", "file_edit",         # FileEditTool
    "edit_notebook", "notebook_edit",  # NotebookEditTool
}

#: Tools that never require permission (read-only / safe-by-design). Mirrors the
#: reference behavior where such tools report ``needsPermissions == false``.
NO_PERMISSION_TOOLS = {
    "read_file", "read_notebook", "read_url_content",
    "search_codebase", "glob_files", "list_dir", "search_web",
    "recall", "remember", "think", "architect", "spawn_subagent",
    "ask_user",
}

#: Tokens that indicate command chaining/substitution and therefore prevent
#: safe prefix verification (Req 10.5).
_INJECTION_TOKENS = (";", "&&", "||", "|", "$(", "`")

#: Commands whose first *two* tokens form a meaningful prefix (e.g. "git commit").
_MULTI_WORD_COMMANDS = {
    "git", "npm", "yarn", "pnpm", "pip", "pip3", "cargo", "go", "docker",
    "kubectl", "dotnet", "apt", "apt-get", "brew", "gh", "poetry", "uv",
    "conda", "bundle", "gem", "make", "terraform",
}

# ---------------------------------------------------------------------------
# Session-only state (file-edit write grant) - not persisted to disk
# ---------------------------------------------------------------------------

_session_write_permission_granted = False

#: The original working directory captured at process start (the dir for which a
#: session write grant applies, mirroring grantWritePermissionForOriginalDir).
_original_cwd = os.getcwd()


def get_original_cwd() -> str:
    """Return the working directory captured at module import time."""
    return _original_cwd


def grant_session_write() -> None:
    """Grant write permission for file-edit tools for the rest of the session."""
    global _session_write_permission_granted
    _session_write_permission_granted = True


def has_session_write() -> bool:
    """Return whether a session write grant is currently active."""
    return _session_write_permission_granted


# Backward-compatible aliases (older callers used the *_permission names).
grant_session_write_permission = grant_session_write
has_session_write_permission = has_session_write


def reset_session_permissions() -> None:
    """Reset all in-memory session permissions (e.g. on /compact or /clear)."""
    global _session_write_permission_granted
    _session_write_permission_granted = False


# ---------------------------------------------------------------------------
# Result + prefix data types
# ---------------------------------------------------------------------------

class PermissionResult:
    """Result of a Permission_Check.

    ``allowed`` indicates authorization; ``message`` carries the denial reason
    when ``allowed`` is False. Truthiness reflects ``allowed`` so existing
    defensive callers (``if result:``) keep working.
    """

    def __init__(self, allowed: bool, message: str = ""):
        self.allowed = allowed
        self.message = message

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PermissionResult(allowed={self.allowed!r}, message={self.message!r})"


#: Result of prefix parsing: the leading subcommand prefix (or None) and whether
#: command injection was detected.
CommandPrefix = namedtuple("CommandPrefix", ["prefix", "injection_detected"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_tool_name(tool: Union[str, Any]) -> str:
    """Return the tool's name whether ``tool`` is a string or a tool instance."""
    if isinstance(tool, str):
        return tool
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else str(tool)


def _is_autonomous(ctx: Any = None) -> bool:
    """Return whether Autonomous_Mode is active (Req 10.8).

    Autonomous mode is signalled either by the ``OMNI_AUTONOMOUS`` environment
    variable or by a truthy ``autonomous`` attribute/key on the supplied context.
    """
    env = os.environ.get("OMNI_AUTONOMOUS", "")
    if env and env.strip().lower() not in ("", "0", "false", "no"):
        return True
    if ctx is None:
        return False
    if isinstance(ctx, dict):
        return bool(ctx.get("autonomous"))
    return bool(getattr(ctx, "autonomous", False))


def _allowed_tools() -> list:
    """Read the project's Allowed_Tools list from the config store."""
    try:
        cfg = config_store.get_project_config()
    except Exception:
        return []
    allowed = cfg.get("allowedTools")
    return list(allowed) if isinstance(allowed, list) else []


def _has_injection(command: str) -> bool:
    """Return True if ``command`` contains chaining/substitution tokens."""
    return any(token in command for token in _INJECTION_TOKENS)


def _command_text(input_args: Dict[str, Any]) -> str:
    """Extract the shell command string from a run_command input dict."""
    if not isinstance(input_args, dict):
        return ""
    cmd = input_args.get("command", "")
    return cmd if isinstance(cmd, str) else str(cmd)


def _is_safe_command(command: str) -> bool:
    """Return True when ``command`` is, or begins with, a Safe_Command (Req 10.2)."""
    stripped = command.strip()
    for safe in SAFE_COMMANDS:
        if stripped == safe or stripped.startswith(safe + " "):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_command_prefix(command: str) -> Optional[CommandPrefix]:
    """Parse a bash command into a leading prefix + injection flag.

    Mirrors ``getCommandSubcommandPrefix`` semantics from the reference: when the
    command contains chaining/substitution (``;``, ``&&``, ``||``, ``|``, ``$(``,
    backticks) the prefix cannot be safely verified, so ``injection_detected`` is
    True and ``prefix`` is None (Req 10.5). For a simple single command the prefix
    is a reasonable leading token sequence: the first two tokens for git-style
    commands, otherwise the first token (Req 10.3).

    Returns ``None`` only when there is no command at all.
    """
    if command is None:
        return None
    stripped = command.strip()
    if not stripped:
        return None

    if _has_injection(command):
        return CommandPrefix(prefix=None, injection_detected=True)

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        # Unbalanced quotes etc. - fall back to whitespace splitting.
        tokens = stripped.split()

    if not tokens:
        return CommandPrefix(prefix=None, injection_detected=False)

    first = tokens[0]
    if len(tokens) >= 2 and first in _MULTI_WORD_COMMANDS:
        prefix = f"{first} {tokens[1]}"
    else:
        prefix = first

    return CommandPrefix(prefix=prefix, injection_detected=False)


def get_permission_key(tool_name: str, input_args: Dict[str, Any], prefix: Optional[str]) -> str:
    """Derive the permission key for a tool invocation.

    - ``run_command`` with a prefix -> ``run_command(<prefix>:*)``
    - ``run_command`` without a prefix -> exact ``run_command(<command>)``
    - any other tool -> the bare ``tool_name``

    (Req 10.3; see the Permission Keys table in the design.)
    """
    if tool_name == BASH_TOOL_NAME:
        if prefix:
            return f"{BASH_TOOL_NAME}({prefix}:*)"
        return f"{BASH_TOOL_NAME}({_command_text(input_args)})"
    return tool_name


def _bash_has_exact_match(command: str, allowed_tools: list) -> bool:
    """Whether ``command`` is authorized by an exact-match key (or is safe)."""
    if _is_safe_command(command):
        return True
    exact_key = get_permission_key(BASH_TOOL_NAME, {"command": command}, None)
    return exact_key in allowed_tools


def has_permission(tool: Union[str, Any], input_args: Dict[str, Any], ctx: Any = None) -> PermissionResult:
    """Authorize or deny a tool invocation (the Permission_Check / ``canUseTool``).

    Decision order:
      1. Autonomous_Mode -> allow all (Req 10.8).
      2. ``run_command``:
         - blanket ``run_command`` in Allowed_Tools -> allow (Req 10.10).
         - Safe_Command -> allow (Req 10.2).
         - injection present -> allow only if an exact-match key exists (Req 10.5).
         - verified prefix matches a ``run_command(<prefix>:*)`` entry -> allow (Req 10.4).
         - exact-match key present -> allow.
         - otherwise -> deny/prompt (Req 10.1).
      3. File-edit tools -> allow if a session write grant is active, else
         deny/prompt; never persisted (Req 10.7).
      4. No-permission (read-only) tools -> allow.
      5. Any other tool -> allow if ``tool_name`` in Allowed_Tools, else deny/prompt (Req 10.1).
    """
    tool_name = _resolve_tool_name(tool)
    input_args = input_args if isinstance(input_args, dict) else {}

    # 1. Autonomous bypass.
    if _is_autonomous(ctx):
        return PermissionResult(True)

    allowed_tools = _allowed_tools()

    # 2. Bash / run_command.
    if tool_name == BASH_TOOL_NAME:
        command = _command_text(input_args)

        # Blanket grant by bare tool name (Req 10.10).
        if BASH_TOOL_NAME in allowed_tools:
            return PermissionResult(True)

        # Safe commands never prompt (Req 10.2).
        if _is_safe_command(command):
            return PermissionResult(True)

        cp = get_command_prefix(command)

        # Command injection: only exact prior approval authorizes (Req 10.5).
        if cp is not None and cp.injection_detected:
            if _bash_has_exact_match(command, allowed_tools):
                return PermissionResult(True)
            return _deny(tool_name)

        # Exact-match key.
        if _bash_has_exact_match(command, allowed_tools):
            return PermissionResult(True)

        # Verified prefix match (Req 10.4).
        if cp is not None and cp.prefix:
            prefix_key = get_permission_key(BASH_TOOL_NAME, input_args, cp.prefix)
            if prefix_key in allowed_tools:
                return PermissionResult(True)

        return _deny(tool_name)

    # 3. File-edit tools: session write grant only, never persisted (Req 10.7).
    if tool_name in FILE_EDIT_TOOLS:
        if has_session_write():
            return PermissionResult(True)
        return _deny(tool_name)

    # 4. Read-only / always-allowed tools.
    if tool_name in NO_PERMISSION_TOOLS:
        return PermissionResult(True)

    # 5. Other tools: persistent Allowed_Tools lookup (Req 10.1).
    if tool_name in allowed_tools:
        return PermissionResult(True)

    return _deny(tool_name)


def _deny(tool_name: str) -> PermissionResult:
    return PermissionResult(
        False,
        f"Permission required to use {tool_name}, but it hasn't been granted yet.",
    )


def save_permission(tool: Union[str, Any], input_args: Dict[str, Any], prefix: Optional[str] = None) -> None:
    """Persist an approval to the project's Allowed_Tools (Req 10.6).

    File-edit tools are special-cased: their approval grants a session-only write
    permission and is NOT written to the Project_Config (Req 10.7).
    """
    tool_name = _resolve_tool_name(tool)
    input_args = input_args if isinstance(input_args, dict) else {}

    # File-edit tools: in-memory session grant only.
    if tool_name in FILE_EDIT_TOOLS:
        grant_session_write()
        return

    key = get_permission_key(tool_name, input_args, prefix)

    cfg = config_store.get_project_config()
    allowed = cfg.get("allowedTools")
    if not isinstance(allowed, list):
        allowed = []
    if key in allowed:
        return
    allowed.append(key)
    allowed.sort()
    cfg["allowedTools"] = allowed
    config_store.save_project_config(cfg)


# ---------------------------------------------------------------------------
# Backward-compatible shim
# ---------------------------------------------------------------------------

async def check_tool_permission(
    tool_name: Union[str, Any],
    input_args: Dict[str, Any],
    skip_permissions: bool = False,
) -> PermissionResult:
    """Backward-compatible async wrapper around :func:`has_permission`.

    Older call sites await ``check_tool_permission``. ``skip_permissions`` maps to
    Autonomous_Mode (bypass the Permission_Check).
    """
    if skip_permissions:
        return PermissionResult(True)
    return has_permission(tool_name, input_args, ctx=None)
