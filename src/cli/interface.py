"""
interface.py - Enhanced Interactive CLI Interface

Sleek, modern interactive interface inspired by Antigravity CLI / Claude Code.

This module is the integration layer that wires the redesigned subsystems
together (task 18.1):

- Visual system + renderer: ``src.cli.theme`` (themed console, glyphs, framing,
  banner, status footer) and ``src.cli.render`` (escape-safe Markdown rendering,
  structured diffs, themed errors) — replacing the old ASCII ``[READ]``/``[CMD]``
  markers and the fake word-by-word ``render_smooth_markdown``.
- Model routing: ``src.model_router.normalize_model`` / ``route`` — the single
  authoritative normalization used by the ``/model`` handler (Req 5.2).
- Permissions: an interactive ``can_use_tool`` approve / approve-and-remember /
  deny callback backed by ``src.permissions`` (Req 10.1, 10.9).
- Persistence: ``src.history`` (command history), ``src.transcript_store``
  (resume/fork), ``src.cost_tracker`` (status footer + budget warnings).
- Onboarding: ``src.cli.onboarding.run_onboarding_if_needed`` (Req 16.7).
- Interrupts: Ctrl+C during a running task sets an ``asyncio.Event`` passed to
  ``agent.execute_task`` so the task is cancelled cleanly without killing the
  REPL (Req 7.11/7.12).

PRESERVED: Cognee memory integration (cognee.add/cognify/search), SimpleMemory
RAG/journaling, and all existing slash commands.
"""
import sys
import os

# ── Centralized Windows UTF-8 enforcement (must run before the Console is built) ──
# Replaces the old ad-hoc chcp/PYTHONUTF8/stdout-wrapping block.
from src.cli import theme as _theme
_theme.enforce_utf8()

import io
import asyncio
import subprocess
import warnings
from dotenv import load_dotenv

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

warnings.filterwarnings("ignore", category=UserWarning)
import logging
import contextlib
for _logger in ["cognee", "OntologyAdapter", "litellm", "httpx", "alembic", "alembic.runtime.migration", "sqlalchemy", "sqlalchemy.engine", "dlt", "urllib3", "asyncio"]:
    logging.getLogger(_logger).setLevel(logging.CRITICAL)
logging.root.setLevel(logging.CRITICAL)
os.environ["COGNEE_SKIP_CONNECTION_TEST"] = "true"
try:
    import loguru
    loguru.logger.remove()
except Exception:
    pass


@contextlib.contextmanager
def suppress_output():
    save_stdout, save_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stdout, sys.stderr = save_stdout, save_stderr


from rich._spinners import SPINNERS
SPINNERS["multi_squares"] = {
    "interval": 80,
    "frames": ["[#  ]", "[## ]", "[###]", "[ ##]", "[  #]", "[   ]"]
}

# ── Themed subsystems ──
from src.cli.theme import (
    make_console, banner as theme_banner, status_footer,
    format_tool_activity, tool_activity_indent,
    user_turn_header, assistant_turn_header, turn_separator, gutter_line,
)
from src.cli.render import render_final, render_error, render_diff
from src import model_router, tool_policy
from src import permissions
from src.permissions import (
    has_permission, save_permission, get_command_prefix, PermissionResult,
)
from src import history as cmd_history
from src import transcript_store
from src.cost_tracker import get_tracker
from src.cli import onboarding
from src import commands as commands_pkg

from src.agent.core import OmniDevAgent

load_dotenv()

# Clean up environment variables loaded from .env to prevent malformed values/whitespaces
for key in list(os.environ.keys()):
    if key.endswith("_API_KEY") or key.endswith("_API_BASE") or key == "OMNI_MODEL":
        val = os.environ[key].strip()
        if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
            val = val[1:-1].strip()
        if not val:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val

# Don't automatically set OLLAMA_API_BASE here - let the agent core logic handle it based on actual model.
if not os.environ.get("OLLAMA_API_BASE"):
    model = os.environ.get("OMNI_MODEL", "")
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if "cloud" in model.lower() and api_key:
        os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
    else:
        os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"


def sync_cognee_config():
    """Sync configuration dynamically into Cognee based on OMNI_MODEL and env vars."""
    try:
        import cognee

        model_name = model_router.normalize_model(
            os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
        )
        provider = "openai"
        model = "gpt-4o"
        api_key = ""
        endpoint = None

        if "/" in model_name:
            parts = model_name.split("/", 1)
            prov_prefix = parts[0].lower()
            model_part = parts[1]

            if prov_prefix == "groq":
                provider = "groq"
                model = model_part
                api_key = os.environ.get("GROQ_API_KEY", "")
            elif prov_prefix == "openai":
                provider = "openai"
                model = model_part
                api_key = os.environ.get("OPENAI_API_KEY", "")
            elif prov_prefix == "anthropic":
                provider = "anthropic"
                model = model_part
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            elif prov_prefix in ("gemini", "vertex_ai"):
                provider = "google_gemini"
                model = model_part
                api_key = os.environ.get("GEMINI_API_KEY", "")
            elif prov_prefix == "openrouter":
                provider = "openrouter"
                model = model_part
                api_key = os.environ.get("OPENROUTER_API_KEY", "")
            elif prov_prefix in ("ollama", "ollama_chat"):
                provider = "ollama"
                model = model_part
                api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
                endpoint = os.environ.get("OLLAMA_API_BASE")
                if endpoint and endpoint.strip().rstrip("/") in ("https://ollama.com", "http://ollama.com"):
                    endpoint = "http://localhost:11434"
            elif prov_prefix == "mistral":
                provider = "mistral"
                model = model_part
                api_key = os.environ.get("MISTRAL_API_KEY", "")
        else:
            lower_m = model_name.lower()
            if "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
                provider = "openai"
                model = model_name
                api_key = os.environ.get("OPENAI_API_KEY", "")
            elif "claude" in lower_m:
                provider = "anthropic"
                model = model_name
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            elif "gemini" in lower_m:
                provider = "google_gemini"
                model = model_name
                api_key = os.environ.get("GEMINI_API_KEY", "")

        cognee.config.set_llm_provider(provider)
        cognee.config.set_llm_model(model)
        if api_key:
            cognee.config.set_llm_api_key(api_key)
        if endpoint:
            cognee.config.set_llm_endpoint(endpoint)

        if provider == "ollama":
            cognee.config.set_embedding_provider("ollama")
            cognee.config.set_embedding_model("nomic-embed-text")
        else:
            if not os.environ.get("OPENAI_API_KEY"):
                cognee.config.set_embedding_provider("fastembed")
            else:
                cognee.config.set_embedding_provider("openai")
                cognee.config.set_embedding_api_key(os.environ["OPENAI_API_KEY"])
    except Exception:
        pass


# ── Themed console (single source of style) ──
console = make_console(highlight=False)


def get_git_branch() -> str:
    """Return the current git branch, or a friendly fallback."""
    try:
        branch = subprocess.check_output(
            "git branch --show-current",
            shell=True, text=True, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        ).strip()
        return branch or "no-git"
    except Exception:
        return "no-git"


def current_model() -> str:
    return os.environ.get("OMNI_MODEL", "groq/openai/gpt-oss-120b").strip()


def print_banner():
    """Print the compact themed banner + a status footer line."""
    console.print()
    console.print(theme_banner(console=console))
    console.print()
    tracker = get_tracker()
    console.print(
        status_footer(
            current_model(), get_git_branch(),
            tracker.total_tokens, tracker.total_cost_usd, console=console,
        )
    )
    console.print()


def render_status_footer():
    """Render the persistent status footer after a turn (Req 15.5)."""
    tracker = get_tracker()
    console.print()
    console.print(
        status_footer(
            current_model(), get_git_branch(),
            tracker.total_tokens, tracker.total_cost_usd, console=console,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Permission prompt (UI side of the injected canUseTool callback) — Req 10.1, 10.9
# ─────────────────────────────────────────────────────────────────────────────
def _tool_name_of(tool) -> str:
    if isinstance(tool, str):
        return tool
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else str(tool)


def _permission_target(tool_name: str, args: dict) -> str:
    if tool_name == "run_command":
        return str(args.get("command", ""))
    return str(args.get("file_path") or args.get("path") or "")


def _ask_permission_choice() -> str:
    """Blocking permission prompt. Returns 'once', 'always', or 'deny'."""
    from rich.prompt import Prompt
    try:
        choice = Prompt.ask(
            "Allow this action? [1] once  [2] always  [3] deny",
            choices=["1", "2", "3"],
            default="1",
        )
    except (EOFError, KeyboardInterrupt):
        return "deny"
    return {"1": "once", "2": "always", "3": "deny"}.get(choice, "deny")


def make_permission_callback(agent, ui_state):
    """Build the interactive ``can_use_tool`` callback injected into the agent.

    Returns a PermissionResult. When the granular permission system denies an
    action (and we are not in Autonomous_Mode), the user is prompted with a
    themed approve / approve-and-remember / deny choice. Approve-and-remember
    persists the permission key (for run_command, derived via the command
    prefix) so the action is auto-approved next time (Req 10.9, 10.3).
    """
    async def can_use_tool(tool, args):
        args = args if isinstance(args, dict) else {}
        ctx = {"autonomous": agent._is_autonomous()}

        result = has_permission(tool, args, ctx=ctx)
        if result.allowed:
            return result

        # Autonomous_Mode should already be granted above, but guard anyway.
        if agent._is_autonomous():
            return PermissionResult(True)

        tool_name = _tool_name_of(tool)
        target = _permission_target(tool_name, args)

        status = ui_state.get("status")
        if status:
            try:
                status.stop()
            except Exception:
                pass
        try:
            body = Text()
            body.append("The agent wants to use ", style="default")
            body.append(tool_name, style="status.warn")
            if target:
                body.append("\n")
                body.append(target, style="app.accent")
            console.print(
                Panel(body, title=Text("Permission required", style="status.warn"),
                      border_style="status.warn", padding=(0, 1))
            )
            choice = await asyncio.get_event_loop().run_in_executor(None, _ask_permission_choice)
        finally:
            if status:
                try:
                    status.start()
                except Exception:
                    pass

        if choice == "once":
            return PermissionResult(True)
        if choice == "always":
            prefix = None
            if tool_name == "run_command":
                cp = get_command_prefix(target)
                prefix = cp.prefix if cp else None
            try:
                save_permission(tool, args, prefix)
            except Exception:
                pass
            return PermissionResult(True)
        return PermissionResult(False, "User denied permission for this action.")

    return can_use_tool


# ─────────────────────────────────────────────────────────────────────────────
# Tool-activity progress callback (single themed code path) — Req 4.1, 4.2
# ─────────────────────────────────────────────────────────────────────────────
def make_tool_callback(ui_state):
    def tool_callback(func_name: str, args: dict):
        args = args if isinstance(args, dict) else {}
        status = ui_state.get("status")

        # Intermediate assistant prose between tool rounds.
        if func_name == "assistant_message":
            content = str(args.get("content", "")).strip()
            if content:
                if status:
                    try:
                        status.stop()
                    except Exception:
                        pass
                console.print()
                console.print(assistant_turn_header(console=console))
                render_final(content, console)
                if status:
                    try:
                        status.start()
                    except Exception:
                        pass
            return

        if status:
            try:
                status.stop()
            except Exception:
                pass
        try:
            # Single themed activity line for every tool (Req 4.2).
            activity = format_tool_activity(func_name, args, console=console)
            console.print(tool_activity_indent(activity, console=console))

            # Surface a structured diff for file edits (Req 14.1), best-effort.
            try:
                if func_name == "write_file":
                    path = str(args.get("path", ""))
                    new = str(args.get("content", ""))
                    old = ""
                    if path and os.path.exists(path):
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            old = f.read()
                    if path:
                        render_diff(old, new, path, console)
                elif func_name == "edit_file":
                    path = str(args.get("file_path", args.get("path", "")))
                    old = str(args.get("old_string", ""))
                    new = str(args.get("new_string", ""))
                    if old or new:
                        render_diff(old, new, path, console)
            except Exception:
                pass
        finally:
            if status:
                try:
                    status.start()
                except Exception:
                    pass

    return tool_callback


# ── Runtime registry of MCP-provided slash-commands ──
# Populated at startup by the MCP integration (Req 13.3). Exposed so the
# completer and `/help` listing include MCP commands too (Req 16.9). Keyed by
# the namespaced command name (``mcp__<server>__<prompt>``) -> MCPCommand.
MCP_COMMANDS: dict = {}


def build_commands_list() -> list:
    """Build the slash-command list for the completer and /help (Req 16.6).

    Includes built-in/ported commands, the interface-native commands, and any
    MCP-provided commands registered at startup (Req 16.9).
    """
    base = ["/" + name for name in commands_pkg.get_all_command_names()]
    # Runtime-only / aliased commands handled directly by this interface.
    extras = [
        "/tokens", "/cost", "/status", "/model", "/api_key", "/memory", "/index",
        "/history", "/commit", "/pwd", "/ls", "/autonomous",
        "/ctx_viz", "/pr_comments", "/release_notes", "/terminal_setup",
        "exit", "quit", "?",
    ]
    # MCP-provided commands discovered at startup (Req 16.9).
    mcp = ["/" + name for name in MCP_COMMANDS]
    return sorted(set(base + extras + mcp))


def _apply_persisted_config(agent) -> None:
    """Apply persisted configuration at startup (Req 9.1, 9.8).

    - Honors a persisted ``activeModel`` (project takes precedence over global)
      when ``OMNI_MODEL`` is not already pinned in the environment.
    - Increments the global ``numStartups`` counter and persists it.

    Fully defensive: any failure is swallowed so a config problem can never
    block startup.
    """
    try:
        from src import config_store
    except Exception:
        return

    # Honor a persisted active model only when the environment hasn't pinned one.
    try:
        if not os.environ.get("OMNI_MODEL", "").strip():
            project_cfg = config_store.get_project_config()
            global_cfg = config_store.get_global_config()
            persisted = None
            if isinstance(project_cfg, dict):
                persisted = project_cfg.get("activeModel")
            if not persisted and isinstance(global_cfg, dict):
                persisted = global_cfg.get("activeModel")
            if persisted:
                canonical = model_router.normalize_model(str(persisted)) or str(persisted)
                if canonical:
                    os.environ["OMNI_MODEL"] = canonical
                    try:
                        agent.model_name = canonical
                    except Exception:
                        pass
    except Exception:
        pass

    # Increment the global startup counter (Req 9.1).
    try:
        global_cfg = config_store.get_global_config()
        global_cfg["numStartups"] = int(global_cfg.get("numStartups", 0) or 0) + 1
        config_store.save_global_config(global_cfg)
    except Exception:
        pass


async def _start_mcp(agent) -> None:
    """Connect configured MCP servers and wire their tools/commands in (Req 13.1).

    Merges ``mcpServers`` from the Global_Config and Project_Config (project
    overrides global), attempts to connect to each, and for every successful
    connection registers the discovered tools into the agent's tool registry and
    the discovered commands into :data:`MCP_COMMANDS`. The agent's tool schemas
    are then rebuilt to include the MCP tools so the model can call them.

    The entire flow is wrapped so a failure (or a missing ``mcp`` SDK / no
    configured servers) is a no-op aside from a surfaced notice and never blocks
    startup (Req 13.4).
    """
    try:
        from src import config_store
        from src.mcp import client as mcp_client
    except Exception:
        return

    try:
        # Merge global + project server maps (project overrides global).
        merged_servers: dict = {}
        try:
            global_cfg = config_store.get_global_config()
            project_cfg = config_store.get_project_config()
            gs = global_cfg.get("mcpServers") if isinstance(global_cfg, dict) else None
            ps = project_cfg.get("mcpServers") if isinstance(project_cfg, dict) else None
            if isinstance(gs, dict):
                merged_servers.update(gs)
            if isinstance(ps, dict):
                merged_servers.update(ps)
        except Exception:
            merged_servers = {}

        connections = await mcp_client.connect_all({"mcpServers": merged_servers})

        for conn in connections:
            try:
                mcp_client.register_tools(conn, agent._tool_instances)
                mcp_client.register_commands(conn, MCP_COMMANDS)
            except Exception:
                continue

        # Rebuild the agent's tool schemas so MCP tools are advertised to the
        # model alongside the native tools.
        if connections:
            try:
                from src.tools import get_json_schemas
                schemas = list(get_json_schemas())
                for tool in agent._tool_instances.values():
                    if isinstance(tool, mcp_client.MCPTool):
                        schemas.append(tool.to_schema())
                agent._tool_schemas = schemas
            except Exception:
                pass

        # Surface any notices (skipped/failed servers, missing SDK) to the user.
        try:
            for notice in mcp_client.notices():
                console.print(f"  [status.warn]{notice}[/status.warn]")
        except Exception:
            pass
    except Exception as exc:
        # MCP must never block startup (Req 13.4).
        try:
            console.print(f"  [app.muted]MCP startup skipped: {exc}[/app.muted]")
        except Exception:
            pass


async def main():
    logging.getLogger("cognee").setLevel(logging.ERROR)
    try:
        import loguru
        loguru.logger.disable("cognee")
    except Exception:
        pass

    console.clear()

    with console.status("[status.ok]Initializing Omni-Dev and loading memories...", spinner="multi_squares"):
        try:
            import cognee as _cog
            _data_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".cognee_data")
            _data_root = os.path.normpath(_data_root)
            os.makedirs(_data_root, exist_ok=True)
            try:
                _cog.config.set_data_root_directory(_data_root)
            except Exception:
                os.environ.setdefault("DATA_ROOT_DIRECTORY", _data_root)

            agent = OmniDevAgent()
            _apply_persisted_config(agent)
            sync_cognee_config()
        except Exception as e:
            render_error(f"Failed to initialize agent: {e}", console)
            return

    # Reflect persisted autonomous flag onto the agent (Req 10.8 toggle).
    agent.autonomous = os.environ.get("OMNI_AUTONOMOUS", "false").strip().lower() in ("1", "true", "yes", "on")

    # ── Load past session memories into agent context (SimpleMemory) ──
    from src.simple_memory import recall as sm_recall, recall_recent, get_memory_summary
    startup_context = ""
    try:
        recent = recall_recent(3)
        relevant = sm_recall("portfolio website hangover ai projects built", top_k=5)
        all_parts = []
        for t in recent + relevant:
            if t and t.strip() and t.strip() not in all_parts:
                all_parts.append(t.strip())
        if all_parts:
            startup_context = "\n\n<previous_session_memory>\n" + "\n\n".join(all_parts[:6]) + "\n</previous_session_memory>"
    except Exception:
        pass

    if startup_context:
        if agent.messages and agent.messages[0]["role"] == "system":
            agent.messages[0]["content"] += startup_context

    console.clear()
    print_banner()
    if startup_context:
        console.print("  [status.ok]Past session memories loaded into context.[/status.ok]")

    # ── First-run trust / onboarding gate (Req 16.7) ──
    try:
        trusted = await onboarding.run_onboarding_if_needed(console)
    except Exception:
        trusted = True
    if not trusted:
        console.print("  [status.warn]Folder not trusted. Exiting.[/status.warn]")
        return

    # ── Shared UI state + injected callbacks ──
    ui_state = {"status": None}
    agent.can_use_tool = make_permission_callback(agent, ui_state)
    tool_callback = make_tool_callback(ui_state)

    # Transcript persistence state (Req 12.4 resume loads it back).
    transcript_state = {"id": None}

    def persist_transcript():
        try:
            t = {
                "id": transcript_state["id"],
                "projectPath": os.getcwd(),
                "model": current_model(),
                "messages": agent.messages,
            }
            transcript_state["id"] = transcript_store.save_transcript(t)
        except Exception:
            pass

    # ── MCP startup: connect configured servers and register tools/commands ──
    # Graceful: never blocks startup; a no-op when no servers are configured or
    # the optional MCP SDK is unavailable (Req 13.1, 13.4).
    MCP_COMMANDS.clear()
    await _start_mcp(agent)

    # ── Interactive PromptSession w/ autocomplete + persistent history ──
    commands_list = build_commands_list()
    use_prompt_toolkit = False
    session = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import InMemoryHistory

        class SlashCommandCompleter(Completer):
            def __init__(self, cmds):
                self.cmds = sorted(cmds)

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor.lstrip()
                if text.startswith("/"):
                    for cmd in self.cmds:
                        if cmd.startswith(text.lower()):
                            yield Completion(cmd, start_position=-len(text))
                elif text.lower() in ("e", "ex", "exi", "exit", "q", "qu", "qui", "quit", "?"):
                    for cmd in ("exit", "quit", "?"):
                        if cmd.startswith(text.lower()):
                            yield Completion(cmd, start_position=-len(text))

        # Seed prompt history so up-arrow recalls past inputs (Req 12.x).
        pt_history = InMemoryHistory()
        try:
            for item in reversed(cmd_history.get_history()):
                if item and item.strip():
                    pt_history.append_string(item)
        except Exception:
            pass

        def _bottom_toolbar():
            tracker = get_tracker()
            sep = " \u00b7 "
            return (
                f"{current_model()}{sep}{get_git_branch()}{sep}"
                f"{tracker.total_tokens:,} tokens{sep}~${tracker.total_cost_usd:.4f}"
            )

        completer = SlashCommandCompleter(commands_list)
        session = PromptSession(completer=completer, history=pt_history, bottom_toolbar=_bottom_toolbar)
        use_prompt_toolkit = True
    except Exception:
        use_prompt_toolkit = False

    while True:
        try:
            console.rule(style="app.muted")
            if use_prompt_toolkit:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: session.prompt("> ")
                )
            else:
                from rich.prompt import Prompt
                user_input = Prompt.ask(" [app.accent]>[/app.accent]")

            cmd = user_input.strip()
            cmd_lower = cmd.lower()

            if not cmd:
                continue

            # Persist command history (skip exit/quit noise is fine; history dedups).
            try:
                cmd_history.add_to_history(cmd)
            except Exception:
                pass

            # Normalized first token for slash-command dispatch (accept - or _).
            # Only treat input as a slash command when it actually begins with
            # "/" — otherwise a normal prompt like "clear the cache" must not
            # trigger /clear.
            first_token = cmd_lower.split()[0]
            norm_cmd = first_token.lstrip("/").replace("-", "_") if first_token.startswith("/") else ""

            # ── Exit ──
            if cmd_lower in ["exit", "quit"]:
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                persist_transcript()
                console.print("  [app.muted]Shutting down Omni-Dev. Goodbye![/app.muted]\n")
                break

            # ── /help or ? ──
            if cmd_lower in ["/help", "?"] or norm_cmd == "help":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                table = Table(title="Available Commands", border_style="app.muted", show_header=True, header_style="app.accent")
                table.add_column("Command", style="app.accent", no_wrap=True)
                table.add_column("Description", style="default")
                # Ported / built-in commands from the central registry (Req 16.9).
                for entry in commands_pkg.COMMANDS:
                    table.add_row("/" + entry["name"], entry["description"])
                # Interface-native commands.
                extra_info = [
                    ("/tokens, /cost", "View live session token usage and cost breakdown"),
                    ("/model [name]", "Switch LLM provider/model (normalized via the router)"),
                    ("/api_key [prov] [key]", "Add an API key securely to .env"),
                    ("/memory", "Query the long-term memory store"),
                    ("/index", "Crawl codebase and push to Cognee Graph Memory"),
                    ("/history", "View agent internal message history"),
                    ("/commit [msg]", "Create a Git commit"),
                    ("/pwd", "Print the current working directory"),
                    ("/ls", "List files in the current directory"),
                    ("/autonomous", "Toggle autonomous mode (skip permission prompts)"),
                    ("exit / quit", "Exit Omni-Dev"),
                ]
                for name, desc in extra_info:
                    table.add_row(name, desc)
                # MCP-provided commands discovered at startup (Req 16.9).
                if MCP_COMMANDS:
                    for mcp_name, mcp_cmd in sorted(MCP_COMMANDS.items()):
                        try:
                            label = mcp_cmd.user_facing_name()
                        except Exception:
                            label = mcp_name
                        desc = getattr(mcp_cmd, "description", "") or ""
                        table.add_row("/" + mcp_name, f"{label} — {desc}" if desc else label)
                console.print(table)
                continue

            # ── /clear (uses clear_command to reset conversation) ──
            if norm_cmd == "clear":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                try:
                    agent.messages = commands_pkg.clear_command(agent)
                except Exception:
                    agent.compact_session()
                try:
                    permissions.reset_session_permissions()
                except Exception:
                    pass
                os.system("cls" if os.name == "nt" else "clear")
                print_banner()
                console.print("  [status.ok]Conversation cleared.[/status.ok]")
                continue

            # ── /autonomous ──
            if norm_cmd == "autonomous":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                new_state = not agent._is_autonomous()
                os.environ["OMNI_AUTONOMOUS"] = "true" if new_state else "false"
                agent.autonomous = new_state
                try:
                    from dotenv import set_key
                    set_key(".env", "OMNI_AUTONOMOUS", "true" if new_state else "false")
                except Exception:
                    pass
                state_str = "[status.ok]ENABLED[/status.ok] (permission prompts disabled)" if new_state else "[status.warn]DISABLED[/status.warn] (permission prompts active)"
                console.print(f"  Autonomous Mode is now {state_str}.")
                continue

            # ── /pwd ──
            if norm_cmd == "pwd":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                console.print(f"  {os.getcwd()}")
                continue

            # ── /ls ──
            if norm_cmd == "ls":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                os.system("dir" if os.name == "nt" else "ls -la")
                continue

            # ── /cost or /tokens or /status ──
            if norm_cmd in ("cost", "tokens", "status"):
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                console.print(Panel(
                    get_tracker().get_summary(),
                    title="[app.accent]Token & Cost Breakdown[/app.accent]",
                    border_style="app.accent",
                ))
                continue

            # ── /history (agent message history) ──
            if norm_cmd == "history":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                if not agent.messages or len(agent.messages) <= 1:
                    console.print("  [status.warn]No session history yet.[/status.warn]")
                else:
                    table = Table(title="Conversation Session History", border_style="app.accent")
                    table.add_column("Role", style="app.accent", width=12)
                    table.add_column("Content / Action", style="default")
                    for msg in agent.messages:
                        role = msg.get("role", "unknown").upper()
                        if role == "SYSTEM":
                            content = msg.get("content", "")
                            preview = content.splitlines()[0] if content else ""
                            table.add_row("SYSTEM", f"[app.muted]{preview[:60]}...[/app.muted]")
                        elif role == "TOOL":
                            name = msg.get("name", "tool")
                            content = msg.get("content", "")
                            table.add_row("TOOL", f"[app.muted]'{name}' -> {len(content)} chars[/app.muted]")
                        else:
                            content = msg.get("content", "") or ""
                            if msg.get("tool_calls"):
                                tc_desc = ", ".join(t.get("function", {}).get("name", "") for t in msg["tool_calls"])
                                content += f" [app.muted](tools: {tc_desc})[/app.muted]"
                            table.add_row(role, str(content).strip()[:200])
                    console.print(table)
                continue

            # ── /commit ──
            if norm_cmd == "commit":
                parts = user_input.strip().split(" ", 1)
                console.print(f"\n[app.accent]/commit[/app.accent]")
                if len(parts) == 2:
                    msg = parts[1].strip()
                    os.system(f'git add -A && git commit -m "{msg}"')
                    console.print(f"  [status.ok]Commit created: '{msg}'[/status.ok]")
                else:
                    console.print("  [status.warn]Usage: /commit <message>[/status.warn]")
                continue

            # ── /compact ──
            if norm_cmd == "compact":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                with console.status("[app.accent]Summarizing conversation before compacting..."):
                    summary, new_messages = await commands_pkg.compact_command(agent.messages, current_model())
                agent.messages = new_messages
                from src.context import invalidate_context_cache
                invalidate_context_cache()
                agent._context = {}
                console.print("  [status.ok]Session compacted successfully.[/status.ok]")
                if summary and not summary.startswith("Error"):
                    console.print(Panel(Markdown(summary[:1500]), title="[app.accent]Saved Summary[/app.accent]", border_style="app.accent"))
                continue

            # ── /init ──
            if norm_cmd == "init":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                with console.status("[app.accent]Analyzing codebase..."):
                    init_prompt = await commands_pkg.init_command()
                console.print("  [app.accent]Agent is creating AGENTS.md...[/app.accent]\n")
                response = await run_agent_task(agent, init_prompt, tool_callback, ui_state)
                console.print(assistant_turn_header(console=console))
                render_final(response, console)
                continue

            # ── /doctor ──
            if norm_cmd == "doctor":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                with console.status("[app.accent]Running diagnostics..."):
                    report = await commands_pkg.doctor_command()
                console.print(Panel(Markdown(report), title="[status.warn]Doctor Report[/status.warn]", border_style="status.warn"))
                continue

            # ── /review ──
            if norm_cmd == "review":
                parts = user_input.strip().split(" ", 1)
                target = parts[1].strip() if len(parts) > 1 else "HEAD"
                console.print(f"\n[app.accent]/review {target}[/app.accent]")
                with console.status(f"[app.accent]Getting git diff for {target}..."):
                    review_prompt = await commands_pkg.review_command(target)
                if review_prompt.startswith("Error") or review_prompt.startswith("No changes"):
                    console.print(f"  [status.warn]{review_prompt}[/status.warn]")
                    continue
                response = await run_agent_task(agent, review_prompt, tool_callback, ui_state)
                console.print(assistant_turn_header(console=console))
                render_final(response, console)
                continue

            # ── /ctx_viz ──
            if norm_cmd == "ctx_viz":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                report = await commands_pkg.ctx_viz_command(agent.messages, agent._context)
                console.print(Panel(Markdown(report), title="[app.accent]Context Visualization[/app.accent]", border_style="app.accent"))
                continue

            # ── /config ──
            if norm_cmd == "config":
                parts = user_input.strip().split(" ", 2)
                key = parts[1].strip() if len(parts) > 1 else None
                value = parts[2].strip() if len(parts) > 2 else None
                console.print(f"\n[app.accent]/config[/app.accent]")
                result = await commands_pkg.config_command(key, value)
                console.print("  " + str(result))
                continue

            # ── /bug ──
            if norm_cmd == "bug":
                parts = user_input.strip().split(" ", 1)
                description = parts[1].strip() if len(parts) > 1 else ""
                console.print(f"\n[app.accent]/bug[/app.accent]")
                if not description:
                    if use_prompt_toolkit:
                        description = await asyncio.get_event_loop().run_in_executor(None, lambda: session.prompt("Describe the bug: "))
                    else:
                        from rich.prompt import Prompt
                        description = Prompt.ask("Describe the bug")
                    description = (description or "").strip()
                report = await commands_pkg.bug_command(description)
                console.print(Panel(Markdown(report), title="[app.accent]Bug Report[/app.accent]", border_style="app.accent"))
                continue

            # ── /pr_comments ──
            if norm_cmd == "pr_comments":
                parts = user_input.strip().split(" ", 1)
                target = parts[1].strip() if len(parts) > 1 else ""
                console.print(f"\n[app.accent]/pr_comments[/app.accent]")
                with console.status("[app.accent]Fetching PR comments..."):
                    report = await commands_pkg.pr_comments_command(target)
                console.print(Panel(Markdown(report), title="[app.accent]PR Comments[/app.accent]", border_style="app.accent"))
                continue

            # ── /release_notes ──
            if norm_cmd in ("release_notes", "releasenotes"):
                console.print(f"\n[app.accent]/release_notes[/app.accent]")
                report = await commands_pkg.release_notes_command()
                console.print(Panel(Markdown(report), title="[app.accent]Release Notes[/app.accent]", border_style="app.accent"))
                continue

            # ── /terminal_setup ──
            if norm_cmd in ("terminal_setup", "terminalsetup"):
                console.print(f"\n[app.accent]/terminal_setup[/app.accent]")
                report = await commands_pkg.terminal_setup_command()
                console.print(Panel(Markdown(report), title="[app.accent]Terminal Setup[/app.accent]", border_style="app.accent"))
                continue

            # ── /resume ──
            if norm_cmd == "resume":
                console.print(f"\n[app.accent]/resume[/app.accent]")
                await handle_resume(agent, transcript_state, session, use_prompt_toolkit)
                continue

            # ── /model ──
            if norm_cmd == "model":
                await handle_model_command(user_input, session, use_prompt_toolkit)
                continue

            # ── /api_key ──
            if norm_cmd == "api_key":
                await handle_api_key_command(user_input, session, use_prompt_toolkit)
                continue

            # ── /index ──
            if norm_cmd == "index":
                import glob
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                with console.status("[app.accent]Indexing codebase into Cognee Graph Memory...", spinner="multi_squares"):
                    import cognee
                    files_added = 0
                    file_texts = []
                    for filepath in glob.glob(os.path.join(".", "**", "*.*"), recursive=True):
                        if any(x in filepath for x in ["node_modules", ".git", "venv", "__pycache__", ".env"]):
                            continue
                        try:
                            file_texts.append(f"Codebase File: {filepath}")
                            files_added += 1
                        except Exception:
                            pass
                    if file_texts:
                        combined = "\n".join(file_texts)
                        try:
                            await cognee.remember(combined, dataset_name="codebase_architecture")
                        except Exception:
                            await cognee.add(combined, dataset_name="codebase_architecture")
                            await cognee.cognify()
                console.print(f"  [status.ok]{files_added} files indexed into Cognee Graph Memory.[/status.ok]")
                continue

            # ── /memory ──
            if norm_cmd == "memory":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                if use_prompt_toolkit:
                    query = await asyncio.get_event_loop().run_in_executor(None, lambda: session.prompt("What memory to recall? (or Enter for recent) "))
                else:
                    from rich.prompt import Prompt
                    query = Prompt.ask("What memory to recall? (or Enter for recent)")
                query = (query or "").strip()
                with console.status("[app.accent]Searching memory store..."):
                    if query:
                        sm_results = sm_recall(query, top_k=10)
                    else:
                        sm_results = recall_recent(10)
                if sm_results:
                    table = Table(title="Long-Term Memories", border_style="app.accent")
                    table.add_column("#", style="app.muted", width=4)
                    table.add_column("Memory / Insight", style="app.accent")
                    for i, text in enumerate(sm_results, 1):
                        table.add_row(str(i), text[:300])
                    console.print(table)
                    console.print(f"  [app.muted]{get_memory_summary()}[/app.muted]")
                else:
                    console.print("  [status.warn]No memories found yet.[/status.warn]")
                continue

            # ── Regular agent task ──
            from src.simple_memory import recall as _sm_recall
            past_context = ""
            try:
                sm_parts = _sm_recall(user_input, top_k=5)
                if sm_parts:
                    past_context = "\n\n<memory_context>\n" + "\n\n".join(sm_parts[:5]) + "\n</memory_context>"
            except Exception:
                pass
            augmented_prompt = f"{user_input}{past_context}"

            # Echo the user turn (themed framing).
            console.print()
            console.print(user_turn_header(console=console))
            console.print(gutter_line(user_input, role="user", console=console))
            console.print(turn_separator("user", console=console))

            final_response = await run_agent_task(agent, augmented_prompt, tool_callback, ui_state)

            # Journaling (SimpleMemory + best-effort background Cognee).
            from src.simple_memory import remember as sm_remember
            try:
                journal_entry = f"[{__import__('time').strftime('%Y-%m-%d')}] User: {user_input[:200]}\nOmni-Dev: {final_response[:400]}"
                sm_remember(journal_entry)
            except Exception:
                pass

            async def _bg_cognee_journal():
                try:
                    with suppress_output():
                        import cognee
                        j = f"User Request: {user_input}\nOmni-Dev Response: {final_response[:600]}"
                        try:
                            await cognee.remember(j, dataset_name="user_memory")
                        except Exception:
                            pass
                except Exception:
                    pass
            asyncio.ensure_future(_bg_cognee_journal())

            # Render the final assistant response.
            console.print()
            if not final_response or not final_response.strip():
                console.print(assistant_turn_header(console=console))
                console.print("  [status.warn]The model returned an empty response.[/status.warn]")
                console.print("  [app.muted]Try /model to switch providers, or /doctor to check your API key.[/app.muted]")
            else:
                console.print(assistant_turn_header(console=console))
                render_final(final_response, console)
                console.print(turn_separator("assistant", console=console))

            # Persist transcript so /resume can restore it (Req 12.4).
            persist_transcript()

            # Status footer + budget warnings (Req 15.5).
            render_status_footer()
            await surface_budget_warnings()

        except KeyboardInterrupt:
            console.print("\n  [app.muted]Interrupted. Type exit to quit.[/app.muted]")
        except Exception:
            console.print_exception(show_locals=False)


# ─────────────────────────────────────────────────────────────────────────────
# Task execution + budget helpers
# ─────────────────────────────────────────────────────────────────────────────
async def run_agent_task(agent, prompt, tool_callback, ui_state):
    """Run the agent with an active spinner; Ctrl+C interrupts cleanly (Req 1.7, 7.11).

    The task is run as an ``asyncio`` future with an ``abort_event``. On
    ``KeyboardInterrupt`` the event is set and the task is awaited again so the
    loop stops at the next checkpoint and history stays consistent — the REPL is
    never killed (Req 7.12).
    """
    status = console.status("[status.ok]Generating...", spinner="multi_squares")
    try:
        status.start()
    except Exception:
        pass
    ui_state["status"] = status

    abort_event = asyncio.Event()
    task = asyncio.ensure_future(
        agent.execute_task(prompt, progress_callback=tool_callback, abort_event=abort_event)
    )
    try:
        final = await task
    except KeyboardInterrupt:
        abort_event.set()
        console.print("\n  [status.warn]Interrupting current task...[/status.warn]")
        try:
            final = await task
        except Exception:
            final = "\u26a0\ufe0f Interrupted."
    except Exception as exc:
        final = f"\U0001f6a8 Error during task: {exc}"
    finally:
        ui_state["status"] = None
        try:
            status.stop()
        except Exception:
            pass
    return final


async def surface_budget_warnings():
    """Surface cost/token threshold warnings with an acknowledge path (Req 15.x)."""
    tracker = get_tracker()
    cost_warn = tracker.check_cost_warning()
    if cost_warn:
        console.print(cost_warn, style="status.warn")
        try:
            from rich.prompt import Prompt
            ans = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Prompt.ask(
                    "Acknowledge and suppress further cost warnings this session?",
                    choices=["y", "n"], default="y",
                ),
            )
            if (ans or "").strip().lower().startswith("y"):
                tracker.acknowledge_cost()
        except Exception:
            pass
    token_warn = tracker.check_token_warning()
    if token_warn:
        console.print(token_warn, style="status.warn")


# ─────────────────────────────────────────────────────────────────────────────
# /resume handler (list / resume / fork) — Req 12.4
# ─────────────────────────────────────────────────────────────────────────────
async def handle_resume(agent, transcript_state, session, use_prompt_toolkit):
    metas = commands_pkg.list_resumable()
    if not metas:
        console.print("  [status.warn]No saved transcripts to resume.[/status.warn]")
        return

    import datetime
    table = Table(title="Resumable Conversations", border_style="app.accent")
    table.add_column("#", style="app.muted", width=4)
    table.add_column("Updated", style="app.muted")
    table.add_column("Model", style="app.accent")
    table.add_column("Msgs", style="app.muted", width=6)
    table.add_column("Preview", style="default")
    for i, m in enumerate(metas, 1):
        ts = m.get("updatedAt") or m.get("createdAt")
        try:
            when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        except Exception:
            when = ""
        table.add_row(str(i), when, str(m.get("model") or ""), str(m.get("messageCount") or 0), str(m.get("preview") or ""))
    console.print(table)

    prompt_str = "Enter # to resume, 'f <#>' to fork, or Enter to cancel: "
    try:
        if use_prompt_toolkit and session is not None:
            choice = await asyncio.get_event_loop().run_in_executor(None, lambda: session.prompt(prompt_str))
        else:
            from rich.prompt import Prompt
            choice = await asyncio.get_event_loop().run_in_executor(None, lambda: Prompt.ask(prompt_str.strip(), default=""))
    except Exception:
        return
    choice = (choice or "").strip()
    if not choice:
        return

    fork = False
    if choice.lower().startswith("f"):
        fork = True
        choice = choice[1:].strip()

    try:
        idx = int(choice) - 1
        meta = metas[idx]
    except Exception:
        console.print("  [status.warn]Invalid selection.[/status.warn]")
        return

    tid = meta.get("id")
    if fork:
        forked = commands_pkg.fork_command(tid, (meta.get("messageCount") or 1) - 1)
        if forked:
            msgs = forked.get("messages")
            if isinstance(msgs, list) and msgs:
                agent.messages = msgs
            transcript_state["id"] = forked.get("id")
            console.print("  [status.ok]Forked and loaded transcript.[/status.ok]")
        else:
            console.print("  [status.warn]Could not fork transcript.[/status.warn]")
        return

    resumed = commands_pkg.resume_command(tid)
    if resumed:
        msgs = resumed.get("messages")
        if isinstance(msgs, list) and msgs:
            agent.messages = msgs
        transcript_state["id"] = tid
        console.print("  [status.ok]Resumed conversation.[/status.ok]")
    else:
        console.print("  [status.warn]Could not load transcript.[/status.warn]")


# ─────────────────────────────────────────────────────────────────────────────
# /model handler (routes every choice through the Model Router) — Req 5.2
# ─────────────────────────────────────────────────────────────────────────────
async def handle_model_command(user_input, session, use_prompt_toolkit):
    parts = user_input.strip().split(" ", 1)
    console.print("\n[app.accent]/model[/app.accent]")
    if len(parts) == 2:
        new_model = parts[1].strip()
    else:
        console.print("  Select an LLM Provider/Model:")
        console.print("    1. Groq (groq/openai/gpt-oss-120b)")
        console.print("    2. Groq (groq/llama-3.3-70b-versatile)")
        console.print("    3. OpenAI (gpt-4o)")
        console.print("    4. Anthropic (claude-3-5-sonnet-20241022)")
        console.print("    5. Google Gemini Studio API (gemini/gemini-1.5-pro)")
        console.print("    6. Google Vertex AI (vertex_ai/gemini-1.5-pro)")
        console.print("    7. OpenRouter Claude 3.5 Sonnet (openrouter/anthropic/claude-3.5-sonnet)")
        console.print("    8. OpenRouter Gemini Pro (openrouter/google/gemini-pro-1.5)")
        console.print("    9. Ollama (Local or Cloud API) (ollama/llama3.3)")
        console.print("   10. Custom model string")
        from rich.prompt import Prompt as RPrompt
        choice = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask("Enter choice (1-10)"))
        choice = (choice or "").strip()
        model_map = {
            "1": "groq/openai/gpt-oss-120b",
            "2": "groq/llama-3.3-70b-versatile",
            "3": "gpt-4o",
            "4": "claude-3-5-sonnet-20241022",
            "5": "gemini/gemini-1.5-pro",
            "6": "vertex_ai/gemini-1.5-pro",
            "7": "openrouter/anthropic/claude-3.5-sonnet",
            "8": "openrouter/google/gemini-pro-1.5",
            "9": "ollama/llama3.3",
        }
        if choice in model_map:
            new_model = model_map[choice]
        else:
            new_model = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask("Enter litellm model string"))
            new_model = (new_model or "").strip()

    if not new_model:
        console.print("  [status.warn]No model provided.[/status.warn]")
        return

    # Single authoritative normalization (Req 5.2).
    canonical = model_router.normalize_model(new_model)
    if not canonical:
        console.print("  [status.warn]Could not parse model name.[/status.warn]")
        return

    os.environ["OMNI_MODEL"] = canonical
    try:
        from dotenv import set_key
        set_key(".env", "OMNI_MODEL", canonical)
    except Exception:
        pass

    decision = model_router.route(canonical, os.environ)

    # Cloud Ollama: point the endpoint at ollama.com so routing/keys line up.
    if decision.is_cloud_ollama:
        base = os.environ.get("OLLAMA_API_BASE")
        if not base or base == "http://localhost:11434":
            os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
            try:
                from dotenv import set_key
                set_key(".env", "OLLAMA_API_BASE", "https://ollama.com")
            except Exception:
                pass
            decision = model_router.route(canonical, os.environ)

    sync_cognee_config()

    console.print(f"  [status.ok]Model switched to:[/status.ok] {decision.canonical_model}")
    if decision.error:
        console.print(f"  [status.warn]{decision.error}[/status.warn]")
    elif not tool_policy.supports_tools(decision):
        console.print("  [status.warn]Note: this model may not support tool use. Responses will be generated without file/command execution.[/status.warn]")


# ─────────────────────────────────────────────────────────────────────────────
# /api_key handler
# ─────────────────────────────────────────────────────────────────────────────
async def handle_api_key_command(user_input, session, use_prompt_toolkit):
    parts = user_input.strip().split(" ", 2)
    console.print("\n[app.accent]/api_key[/app.accent]")
    if len(parts) == 3:
        provider_key = parts[1].strip().upper()
        if not provider_key.endswith("_API_KEY"):
            provider_key += "_API_KEY"
        key_value = parts[2].strip()
    else:
        console.print("  Select API Provider:")
        console.print("    1. Groq (GROQ_API_KEY)")
        console.print("    2. OpenAI (OPENAI_API_KEY)")
        console.print("    3. Anthropic (ANTHROPIC_API_KEY)")
        console.print("    4. Google Gemini Studio API (GEMINI_API_KEY)")
        console.print("    5. Google Vertex AI Project ID (VERTEXAI_PROJECT)")
        console.print("    6. Google Vertex AI Location (VERTEXAI_LOCATION)")
        console.print("    7. OpenRouter (OPENROUTER_API_KEY)")
        console.print("    8. Mistral (MISTRAL_API_KEY)")
        console.print("    9. Ollama Cloud API Base URL (OLLAMA_API_BASE)")
        console.print("   10. Ollama Cloud API Key (OLLAMA_API_KEY)")
        console.print("   11. Custom env var name")
        from rich.prompt import Prompt as RPrompt
        choice = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask("Enter choice (1-11)"))
        choice = (choice or "").strip()
        provider_map = {
            "1": "GROQ_API_KEY",
            "2": "OPENAI_API_KEY",
            "3": "ANTHROPIC_API_KEY",
            "4": "GEMINI_API_KEY",
            "5": "VERTEXAI_PROJECT",
            "6": "VERTEXAI_LOCATION",
            "7": "OPENROUTER_API_KEY",
            "8": "MISTRAL_API_KEY",
            "9": "OLLAMA_API_BASE",
            "10": "OLLAMA_API_KEY",
        }
        if choice in provider_map:
            provider_key = provider_map[choice]
        else:
            provider_key = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask("Enter env var name"))
            provider_key = (provider_key or "").strip().upper()
            if "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key and not provider_key.endswith("_API_KEY"):
                provider_key += "_API_KEY"

        is_secret = "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key
        key_value = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask(f"Enter {provider_key}", password=is_secret))
        key_value = (key_value or "").strip()

    if key_value:
        os.environ[provider_key] = key_value
        if provider_key == "OLLAMA_API_KEY":
            current = os.environ.get("OMNI_MODEL", "").lower()
            if "cloud" in current and "ollama" in current:
                if not os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_API_BASE") == "http://localhost:11434":
                    os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
                    try:
                        from dotenv import set_key
                        set_key(".env", "OLLAMA_API_BASE", "https://ollama.com")
                    except Exception:
                        pass
        try:
            from dotenv import set_key
            set_key(".env", provider_key, key_value)
        except Exception:
            pass
        sync_cognee_config()
        console.print(f"  [status.ok]API key saved:[/status.ok] {provider_key}")


if __name__ == "__main__":
    asyncio.run(main())
