"""MCP (Model Context Protocol) client for Omni-Dev.

Connects to configured MCP servers, discovers their tools and prompts, and
registers them into the agent's tool/command registries. Discovered tools are
wrapped as :class:`MCPTool` (a :class:`~src.tools.base_tool.BaseTool` subclass)
so they participate in the Agent_Loop exactly like native tools, flowing through
the same Tool_Input_Validation, Permission_Check, ordering, and truncation path
(Requirements 13.2, 13.5).

Design goals (Requirement 13):
- 13.1  Server config comes from Global_Config / Project_Config ``mcpServers``.
- 13.2  Discovered tools are registered and usable by the agent.
- 13.3  Discovered prompts are registered as available slash-commands.
- 13.4  A single failing server NEVER crashes the CLI; the failure is recorded
        as a notice and the remaining servers/capabilities keep working.
- 13.5  MCP tools subclass ``BaseTool`` so they reuse the native tool pipeline.
- 13.6  Server approval decisions are persisted to config.

Optional dependency
-------------------
Full functionality requires the official MCP Python SDK::

    pip install mcp

This module guards that import: if the ``mcp`` package is absent the module
still imports cleanly, ``connect_all`` returns ``[]`` (with a notice), and the
CLI continues to run with only its native tools. ``python -c "import
src.mcp.client"`` always succeeds.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Mapping, Optional

from src.tools.base_tool import BaseTool

# ---------------------------------------------------------------------------
# Optional MCP SDK import (guarded -- never make this a hard dependency)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised indirectly; import availability varies
    from mcp import ClientSession, StdioServerParameters  # type: ignore
    from mcp.client.stdio import stdio_client  # type: ignore

    _MCP_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import problem must degrade gracefully
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore
    _MCP_AVAILABLE = False


#: Default per-connection timeout (seconds) applied to connect/initialize and
#: discovery so a stuck server cannot hang the CLI.
CONNECT_TIMEOUT = 15.0

#: Module-level accumulator of human-readable notices from the most recent
#: ``connect_all`` run (connection failures, skipped servers, missing SDK).
#: Surfaced by the interface layer so users understand what happened.
_NOTICES: List[str] = []


def is_sdk_available() -> bool:
    """Return True if the optional ``mcp`` Python SDK is importable."""
    return _MCP_AVAILABLE


def notices() -> List[str]:
    """Return a copy of the notices collected by the most recent ``connect_all``."""
    return list(_NOTICES)


# ---------------------------------------------------------------------------
# Content stringification
# ---------------------------------------------------------------------------

def _stringify_content(content: Any) -> str:
    """Reduce an MCP tool-call result into a plain string for the Agent_Loop.

    MCP results commonly carry a ``content`` list of typed blocks (text, image,
    ...). Text blocks are concatenated; non-text blocks are described. Scalar
    or unknown shapes fall back to ``str(content)``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: List[str] = []
        for item in content:
            text = getattr(item, "text", None)
            item_type = getattr(item, "type", None)
            if text is not None:
                parts.append(str(text))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            elif item_type == "image" or (isinstance(item, dict) and item.get("type") == "image"):
                parts.append("[image content]")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# MCPTool: adapts a discovered MCP tool to the project's BaseTool interface
# ---------------------------------------------------------------------------

class MCPTool(BaseTool):
    """A discovered MCP tool exposed through the native ``BaseTool`` interface.

    Registering this into the tool registry lets the Agent_Loop validate,
    permission-gate, order, and truncate MCP tool calls identically to native
    tools (Req 13.5). The registered name is namespaced ``mcp__<server>__<tool>``
    to avoid collisions with native tools and other servers.
    """

    def __init__(
        self,
        connection: "MCPConnection",
        tool_name: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._connection = connection
        self._tool_name = tool_name
        self._description = description or f"MCP tool '{tool_name}' from server '{connection.name}'"
        self._input_schema = input_schema if isinstance(input_schema, dict) else {
            "type": "object",
            "properties": {},
        }
        self._registered_name = f"mcp__{connection.name}__{tool_name}"

    @property
    def name(self) -> str:
        return self._registered_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        props = self._input_schema.get("properties") if isinstance(self._input_schema, dict) else None
        return props if isinstance(props, dict) else {}

    @property
    def required_params(self) -> List[str]:
        req = self._input_schema.get("required") if isinstance(self._input_schema, dict) else None
        return list(req) if isinstance(req, (list, tuple)) else []

    def is_read_only(self) -> bool:
        # The MCP protocol does not advertise read-only semantics, so we treat
        # external tools conservatively as mutating (Req 13.5: same path, but
        # default to the safe, serial branch).
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        # External tools always go through the Permission_Check unless the loop
        # is in Autonomous_Mode (Req 13.5).
        return True

    def to_schema(self) -> Dict[str, Any]:
        # Use the server-provided JSON schema verbatim so litellm sees the exact
        # input contract the server declared.
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._input_schema,
            },
        }

    async def call(self, **kwargs: Any) -> str:
        """Invoke the underlying MCP tool and return a string result."""
        return await self._connection.call_tool(self._tool_name, kwargs)


# ---------------------------------------------------------------------------
# MCPCommand: a discovered MCP prompt exposed as a slash-command
# ---------------------------------------------------------------------------

class MCPCommand:
    """A discovered MCP prompt registered as an available slash-command (Req 13.3)."""

    def __init__(
        self,
        connection: "MCPConnection",
        prompt_name: str,
        description: str = "",
        arg_names: Optional[List[str]] = None,
    ) -> None:
        self._connection = connection
        self._prompt_name = prompt_name
        self.description = description or f"MCP prompt '{prompt_name}' from server '{connection.name}'"
        self.arg_names = list(arg_names or [])
        self.name = f"mcp__{connection.name}__{prompt_name}"

    def user_facing_name(self) -> str:
        return f"{self._connection.name}:{self._prompt_name} (MCP)"

    async def get_prompt(self, args: Optional[Dict[str, str]] = None) -> Any:
        """Resolve this prompt into messages by calling the MCP server."""
        return await self._connection.get_prompt(self._prompt_name, args or {})


# ---------------------------------------------------------------------------
# MCPConnection: a live (or attempted) connection to one MCP server
# ---------------------------------------------------------------------------

class MCPConnection:
    """Represents a connection to a single configured MCP server.

    Holds the live session (when the SDK is present and the connection
    succeeded) plus the discovered tool/prompt descriptors. The session's async
    resources are owned by an :class:`AsyncExitStack` so :meth:`close` tears
    everything down cleanly.
    """

    def __init__(self, name: str, server_config: Mapping[str, Any]) -> None:
        self.name = name
        self.config: Dict[str, Any] = dict(server_config or {})
        self.session: Any = None
        self.connected: bool = False
        self.tools: List[Dict[str, Any]] = []      # [{name, description, inputSchema}]
        self.commands: List[Dict[str, Any]] = []    # [{name, description, argNames}]
        self._exit_stack: Optional[AsyncExitStack] = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, timeout: float = CONNECT_TIMEOUT) -> None:
        """Open the stdio transport, initialize the session, and discover capabilities.

        Raises on failure; callers (``connect_all``) wrap this in try/except so
        one bad server cannot crash the CLI (Req 13.4).
        """
        if not _MCP_AVAILABLE:
            raise RuntimeError(
                "MCP SDK not installed; run 'pip install mcp' to enable MCP support"
            )

        command = self.config.get("command")
        if not command:
            raise ValueError(f"MCP server '{self.name}' is missing a 'command'")

        env = dict(os.environ)
        extra_env = self.config.get("env")
        if isinstance(extra_env, dict):
            env.update({str(k): str(v) for k, v in extra_env.items()})

        params = StdioServerParameters(  # type: ignore[call-arg]
            command=command,
            args=list(self.config.get("args", []) or []),
            env=env,
        )

        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))  # type: ignore[misc]
        self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))  # type: ignore[misc]
        await asyncio.wait_for(self.session.initialize(), timeout=timeout)
        self.connected = True
        await self._discover(timeout=timeout)

    async def _discover(self, timeout: float = CONNECT_TIMEOUT) -> None:
        """Populate ``self.tools`` and ``self.commands`` from the server.

        Discovery of tools and prompts is independent: a server may support one
        but not the other, so each is guarded so a missing capability does not
        abort the whole discovery.
        """
        # Tools
        try:
            resp = await asyncio.wait_for(self.session.list_tools(), timeout=timeout)
            for tool in getattr(resp, "tools", []) or []:
                self.tools.append(
                    {
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", "") or "",
                        "inputSchema": getattr(tool, "inputSchema", None)
                        or getattr(tool, "input_schema", None)
                        or {"type": "object", "properties": {}},
                    }
                )
        except Exception:  # noqa: BLE001 - server may not support tools
            pass

        # Prompts -> commands
        try:
            resp = await asyncio.wait_for(self.session.list_prompts(), timeout=timeout)
            for prompt in getattr(resp, "prompts", []) or []:
                raw_args = getattr(prompt, "arguments", None) or []
                arg_names = [getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else None) for a in raw_args]
                arg_names = [a for a in arg_names if a]
                self.commands.append(
                    {
                        "name": getattr(prompt, "name", None),
                        "description": getattr(prompt, "description", "") or "",
                        "argNames": arg_names,
                    }
                )
        except Exception:  # noqa: BLE001 - server may not support prompts
            pass

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Call a tool on this server and return its result as a string."""
        if not self.connected or self.session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        result = await self.session.call_tool(tool_name, arguments=args)
        if getattr(result, "isError", False):
            content = _stringify_content(getattr(result, "content", None))
            raise RuntimeError(f"MCP tool '{tool_name}' error: {content}")
        return _stringify_content(getattr(result, "content", result))

    async def get_prompt(self, prompt_name: str, args: Dict[str, str]) -> Any:
        """Resolve a prompt on this server into its message payload."""
        if not self.connected or self.session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        return await self.session.get_prompt(prompt_name, arguments=args)

    async def close(self) -> None:
        """Tear down the session and transport, ignoring shutdown errors."""
        self.connected = False
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            finally:
                self._exit_stack = None
                self.session = None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _extract_servers(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return the ``{name: serverConfig}`` map from a flexible ``config`` input.

    Accepts either a Global_Config/Project_Config dict that contains an
    ``mcpServers`` key, or the ``mcpServers`` map directly.
    """
    if not isinstance(config, Mapping):
        return {}
    if "mcpServers" in config and isinstance(config["mcpServers"], Mapping):
        return dict(config["mcpServers"])
    # Otherwise assume the mapping IS the servers map (skip obvious non-server keys).
    return {k: v for k, v in config.items() if isinstance(v, Mapping)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def connect_all(
    config: Optional[Mapping[str, Any]],
    auto_approve: bool = False,
) -> List[MCPConnection]:
    """Connect to every configured & approved MCP server, tolerating failures.

    Iterates the configured servers (Req 13.1). For each server:

    - ``sse``/``url`` transports are skipped with a notice (only ``stdio`` is
      supported by this client).
    - Servers not marked ``approved`` are skipped unless ``auto_approve`` is set
      (Req 13.6).
    - Connection is attempted inside a per-server ``try/except`` so one failing
      server records a notice and does NOT crash the CLI or block the others
      (Req 13.4).

    Returns the list of successfully connected :class:`MCPConnection` objects.
    Notices for skipped/failed servers are available via :func:`notices`.
    """
    global _NOTICES
    _NOTICES = []
    connections: List[MCPConnection] = []

    servers = _extract_servers(config)
    if not servers:
        return connections

    if not _MCP_AVAILABLE:
        _NOTICES.append(
            "MCP servers are configured but the 'mcp' package is not installed; "
            "skipping MCP. Run 'pip install mcp' to enable MCP support."
        )
        return connections

    for name, server_cfg in servers.items():
        if not isinstance(server_cfg, Mapping):
            _NOTICES.append(f"MCP server '{name}' has an invalid configuration; skipped.")
            continue

        server_type = (server_cfg.get("type") or "stdio").lower()
        if server_type in ("sse", "url", "http") or server_cfg.get("url"):
            _NOTICES.append(
                f"MCP server '{name}' uses unsupported transport '{server_type}'; "
                "only 'stdio' servers are supported; skipped."
            )
            continue

        if not (auto_approve or server_cfg.get("approved")):
            _NOTICES.append(
                f"MCP server '{name}' is not approved; skipped. "
                "Approve it to enable its tools and commands."
            )
            continue

        conn = MCPConnection(name, server_cfg)
        try:
            await conn.connect()
            connections.append(conn)
        except Exception as exc:  # noqa: BLE001 - graceful per-server failure (Req 13.4)
            await conn.close()
            _NOTICES.append(f"Failed to connect to MCP server '{name}': {exc}")
            continue

    return connections


def _register_into(registry: Any, key: str, value: Any) -> None:
    """Insert ``value`` (named ``key``) into a flexible registry container.

    Supports the registry being a dict (``registry[key] = value``), an object
    exposing ``register(value)`` / ``add(value)``, or a list (``append``).
    """
    if registry is None:
        return
    if isinstance(registry, dict):
        registry[key] = value
        return
    register_fn = getattr(registry, "register", None)
    if callable(register_fn):
        register_fn(value)
        return
    add_fn = getattr(registry, "add", None)
    if callable(add_fn):
        add_fn(value)
        return
    append_fn = getattr(registry, "append", None)
    if callable(append_fn):
        append_fn(value)
        return
    raise TypeError(f"Unsupported registry type: {type(registry)!r}")


def register_tools(conn: MCPConnection, registry: Any) -> None:
    """Wrap each of ``conn``'s discovered tools as an :class:`MCPTool` and register it.

    Registered tools share the native tool pipeline so they are validated,
    permission-gated, ordered, and truncated like any built-in tool
    (Req 13.2, 13.5).
    """
    for descriptor in conn.tools:
        tool_name = descriptor.get("name")
        if not tool_name:
            continue
        tool = MCPTool(
            connection=conn,
            tool_name=tool_name,
            description=descriptor.get("description", ""),
            input_schema=descriptor.get("inputSchema"),
        )
        _register_into(registry, tool.name, tool)


def register_commands(conn: MCPConnection, command_registry: Any) -> None:
    """Register each of ``conn``'s discovered prompts as an MCP slash-command (Req 13.3)."""
    for descriptor in conn.commands:
        prompt_name = descriptor.get("name")
        if not prompt_name:
            continue
        command = MCPCommand(
            connection=conn,
            prompt_name=prompt_name,
            description=descriptor.get("description", ""),
            arg_names=descriptor.get("argNames"),
        )
        _register_into(command_registry, command.name, command)


def approve_server(name: str, path: Optional[str] = None) -> None:
    """Persist approval for the MCP server ``name`` by setting ``approved=True`` (Req 13.6).

    Looks for the server in the Project_Config first (highest precedence), then
    the Global_Config, and saves the approval back to whichever config holds it.
    If the server is not yet present in either config, it is recorded as an
    approved entry in the Project_Config so the decision persists.
    """
    # Imported lazily so this module stays import-safe and decoupled.
    from src import config_store

    project_cfg = config_store.get_project_config(path)
    project_servers = project_cfg.get("mcpServers")
    if isinstance(project_servers, dict) and name in project_servers:
        if not isinstance(project_servers[name], dict):
            project_servers[name] = {}
        project_servers[name]["approved"] = True
        config_store.save_project_config(project_cfg, path)
        return

    global_cfg = config_store.get_global_config()
    global_servers = global_cfg.get("mcpServers")
    if isinstance(global_servers, dict) and name in global_servers:
        if not isinstance(global_servers[name], dict):
            global_servers[name] = {}
        global_servers[name]["approved"] = True
        config_store.save_global_config(global_cfg)
        return

    # Not previously known: record an approved placeholder in the project config.
    if not isinstance(project_servers, dict):
        project_servers = {}
    project_servers[name] = {"approved": True}
    project_cfg["mcpServers"] = project_servers
    config_store.save_project_config(project_cfg, path)
