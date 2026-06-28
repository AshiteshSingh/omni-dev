"""
Commands package - Python conversion of scratch_repo/src/commands/

All slash commands available in the CLI.
"""
from .compact import compact_command
from .init_cmd import init_command
from .doctor import doctor_command
from .review import review_command
from .ctx_viz import ctx_viz_command
from .config_cmd import config_command
from .bug import bug_command
from .pr_comments import pr_comments_command
from .release_notes import release_notes_command
from .terminal_setup import terminal_setup_command
from .clear import clear_command
from .resume import list_resumable, resume_command, fork_command

#: Registry of built-in / ported commands. The interface (and the `/help`
#: listing, Req 16.9) reads this so every command — including the ported utility
#: commands — is discoverable in one place. MCP-provided commands are appended to
#: this list at runtime by the dispatcher (task 18).
COMMANDS = [
    {"name": "compact", "description": "Summarize the conversation, then clear it (preserves long-term memory)."},
    {"name": "init", "description": "Analyze the codebase and create/update AGENTS.md."},
    {"name": "doctor", "description": "Diagnose the environment: API keys, tools, dependencies, memory."},
    {"name": "review", "description": "AI code review of recent git changes."},
    {"name": "ctx-viz", "description": "Visualize the current conversation context and token usage."},
    {"name": "config", "description": "View or edit configuration values."},
    {"name": "bug", "description": "Capture a bug report with environment info and store it locally."},
    {"name": "pr-comments", "description": "Fetch and summarize GitHub pull-request review comments."},
    {"name": "release-notes", "description": "Show the release notes / changelog."},
    {"name": "terminal-setup", "description": "Configure a Shift+Enter newline key binding for your terminal."},
    {"name": "clear", "description": "Reset the conversation history and free up context."},
    {"name": "resume", "description": "List, resume, or fork a previous conversation."},
    {"name": "help", "description": "List all available commands."},
]


def get_all_command_names() -> list:
    """Return the names of all built-in / ported commands (Req 16.9)."""
    return [cmd["name"] for cmd in COMMANDS]


__all__ = [
    "compact_command",
    "init_command",
    "doctor_command",
    "review_command",
    "ctx_viz_command",
    "config_command",
    "bug_command",
    "pr_comments_command",
    "release_notes_command",
    "terminal_setup_command",
    "clear_command",
    "list_resumable",
    "resume_command",
    "fork_command",
    "COMMANDS",
    "get_all_command_names",
]
