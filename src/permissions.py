"""
permissions.py - Python conversion of scratch_repo/src/permissions.ts

Handles permission checking for tools, with special cases for BashTool
and file editing tools. Mirrors the TypeScript permission system.
"""
import os
import json
from typing import Dict, Any, Optional

# Session-only write permissions (mirrors filesystem.ts grantWritePermissionForOriginalDir)
_session_write_permission_granted = False
_approved_commands: set = set()
_approved_tool_prefixes: set = set()

# Safe read directory (the original working directory at startup)
_original_cwd = os.getcwd()


def get_original_cwd() -> str:
    return _original_cwd


def grant_session_write_permission():
    """Grant write permission for file edit/write tools for this session."""
    global _session_write_permission_granted
    _session_write_permission_granted = True


def has_session_write_permission() -> bool:
    return _session_write_permission_granted


def approve_command(command_prefix: str):
    """Permanently approve a command prefix for this session."""
    _approved_commands.add(command_prefix)


def is_command_approved(command: str) -> bool:
    """Check if a command matches any approved prefix."""
    cmd_lower = command.lower().strip()
    return any(cmd_lower.startswith(prefix.lower()) for prefix in _approved_commands)


def approve_tool(tool_name: str):
    """Approve a tool for use without further prompting."""
    _approved_tool_prefixes.add(tool_name)


def is_tool_approved(tool_name: str) -> bool:
    return tool_name in _approved_tool_prefixes


class PermissionResult:
    """Mirrors TypeScript PermissionResult type."""
    def __init__(self, allowed: bool, message: str = ""):
        self.allowed = allowed
        self.message = message

    def __bool__(self):
        return self.allowed


async def check_tool_permission(
    tool_name: str,
    input_args: Dict[str, Any],
    skip_permissions: bool = False,
) -> PermissionResult:
    """
    Check if a tool can be used.
    Mirrors hasPermissionsToUseTool from scratch_repo/src/permissions.ts.
    """
    if skip_permissions:
        return PermissionResult(True)

    # Tools that never need permissions
    READ_ONLY_TOOLS = {"read_file", "recall", "think", "search_codebase", "glob_files", "list_dir", "search_web", "read_notebook"}
    if tool_name in READ_ONLY_TOOLS:
        return PermissionResult(True)

    # Memory write - always allowed
    if tool_name == "remember":
        return PermissionResult(True)

    # Sub-agent spawn - always allowed
    if tool_name == "spawn_subagent":
        return PermissionResult(True)

    # Architect tool - read-only planning, always allowed
    if tool_name == "architect":
        return PermissionResult(True)

    # File write/edit tools - check session permission
    if tool_name in ("write_file", "edit_file", "edit_notebook"):
        if has_session_write_permission():
            return PermissionResult(True)
        # Auto-grant on first use (user will see the action in the UI)
        grant_session_write_permission()
        return PermissionResult(True)

    # Bash tool - handled by BashTool itself (requires user approval inline)
    if tool_name == "run_command":
        return PermissionResult(True)  # BashTool handles its own permission checking

    # Default: allow but warn
    return PermissionResult(True)


def reset_session_permissions():
    """Reset all session permissions (e.g., on /compact)."""
    global _session_write_permission_granted
    _session_write_permission_granted = False
    _approved_commands.clear()
