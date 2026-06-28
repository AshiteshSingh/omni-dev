"""Model Context Protocol (MCP) support for Omni-Dev.

This package connects to configured MCP servers and registers their discovered
tools and prompts/commands into the agent's registries so that external MCP
capabilities flow through the *same* validation, permission, ordering, and
truncation path as native tools (Requirement 13).

The package is fully import-safe even when the optional ``mcp`` Python SDK is
not installed: ``import src.mcp.client`` always succeeds, and
``connect_all`` degrades gracefully (returns ``[]`` with an explanatory notice)
rather than crashing the CLI.
"""

from .client import (  # noqa: F401
    MCPConnection,
    MCPTool,
    MCPCommand,
    connect_all,
    register_tools,
    register_commands,
    approve_server,
    notices,
    is_sdk_available,
)

__all__ = [
    "MCPConnection",
    "MCPTool",
    "MCPCommand",
    "connect_all",
    "register_tools",
    "register_commands",
    "approve_server",
    "notices",
    "is_sdk_available",
]
