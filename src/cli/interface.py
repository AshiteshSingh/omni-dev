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

# Pin Cognee's DATA + SYSTEM storage roots into the project's .cognee_data dir at
# import time — BEFORE any cognee operation — so the knowledge graph is durable
# and reinstall-proof (never under site-packages). Side-effecting import.
from src import cognee_paths  # noqa: F401

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
# NOTE: litellm is intentionally NOT imported here — importing it is slow (several
# seconds) and would block CLI startup. Its quiet-mode flags are applied lazily in
# model_router.get_completion_fn() the first time a model call is actually made.
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
# Smooth braille spinner used for the agent "thinking" animation — the same
# style modern coding CLIs use. 10 frames at 80ms reads as a continuous pulse.
SPINNERS["omni_pulse"] = {
    "interval": 80,
    "frames": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
}

# ── Themed subsystems ──
from src.cli.theme import (
    make_console, banner as theme_banner, status_footer,
    format_tool_activity, tool_activity_indent, format_tool_result,
    THINKING_VERBS,
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
from src.cli import ui_state
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
    """Sync configuration dynamically into Cognee based on OMNI_MODEL and env vars.

    Maps the active OMNI_MODEL onto a VALID Cognee LLM provider. Cognee v1.2.2
    accepts these providers: openai, anthropic, gemini, ollama, mistral, custom,
    azure, bedrock, llama_cpp. Anything that does not map cleanly (Vertex AI,
    Groq, OpenRouter, ...) is routed through the ``custom`` provider with the full
    litellm model string (e.g. ``vertex_ai/gemini-2.5-pro``), which Cognee passes
    straight to litellm. The chosen hackathon path is Vertex AI via ADC creds.

    Fully defensive — every cognee setter is wrapped so a config problem can never
    crash the CLI.
    """
    try:
        # Ensure durable storage roots are pinned before any cognee operation.
        try:
            from src import cognee_paths
            cognee_paths.configure_cognee_storage()
        except Exception:
            pass

        # No model configured yet: nothing to map onto Cognee. Do NOT assume a
        # provider/model here — the user picks one via /model or OMNI_MODEL.
        raw_model = os.environ.get("OMNI_MODEL", "").strip()
        if not raw_model:
            return

        import cognee

        model_name = model_router.normalize_model(raw_model)

        # Defaults (OpenAI) — overridden below based on the parsed provider.
        provider = "openai"
        model = "gpt-4o"
        api_key = ""
        endpoint = None

        if "/" in model_name:
            prov_prefix, model_part = model_name.split("/", 1)
            prov_prefix = prov_prefix.lower()

            if prov_prefix == "vertex_ai":
                # Vertex AI gemini via litellm. Cognee has no native vertex
                # provider, so use "custom" with the FULL litellm string. Auth
                # comes from ADC (GOOGLE_APPLICATION_CREDENTIALS + VERTEXAI_*),
                # but some adapters require a non-empty key, so set a placeholder.
                provider = "custom"
                model = model_name  # "vertex_ai/<model>"
                api_key = os.environ.get("GOOGLE_API_KEY", "") or "vertex-adc"
            elif prov_prefix == "gemini":
                # AI Studio Gemini — native cognee provider.
                provider = "gemini"
                model = model_part
                api_key = os.environ.get("GEMINI_API_KEY", "")
            elif prov_prefix == "openai":
                provider = "openai"
                model = model_part
                api_key = os.environ.get("OPENAI_API_KEY", "")
            elif prov_prefix == "anthropic":
                provider = "anthropic"
                model = model_part
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            elif prov_prefix == "mistral":
                provider = "mistral"
                model = model_part
                api_key = os.environ.get("MISTRAL_API_KEY", "")
            elif prov_prefix in ("ollama", "ollama_chat"):
                provider = "ollama"
                model = model_part
                api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
                endpoint = os.environ.get("OLLAMA_API_BASE")
                if endpoint and endpoint.strip().rstrip("/") in ("https://ollama.com", "http://ollama.com"):
                    endpoint = "http://localhost:11434"
            elif prov_prefix == "groq":
                # No native groq provider — route via custom + full litellm string.
                provider = "custom"
                model = model_name  # "groq/<model>"
                api_key = os.environ.get("GROQ_API_KEY", "")
            elif prov_prefix == "openrouter":
                provider = "custom"
                model = model_name  # "openrouter/<model>"
                api_key = os.environ.get("OPENROUTER_API_KEY", "")
            else:
                # Any other prefixed model (deepseek, cohere, huggingface, ...).
                provider = "custom"
                model = model_name
                api_key = (
                    os.environ.get(prov_prefix.upper() + "_API_KEY", "")
                    or os.environ.get("OPENROUTER_API_KEY", "")
                )
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
                provider = "gemini"
                model = model_name
                api_key = os.environ.get("GEMINI_API_KEY", "")

        def _try(method_name, *args):
            fn = getattr(cognee.config, method_name, None)
            if callable(fn):
                try:
                    fn(*args)
                except Exception:
                    pass

        _try("set_llm_provider", provider)
        _try("set_llm_model", model)
        if api_key:
            _try("set_llm_api_key", api_key)
        if endpoint:
            _try("set_llm_endpoint", endpoint)

        # ── Embeddings ──
        if provider == "ollama":
            _try("set_embedding_provider", "ollama")
            _try("set_embedding_model", "nomic-embed-text")
        elif os.environ.get("OPENAI_API_KEY"):
            _try("set_embedding_provider", "openai")
            _try("set_embedding_api_key", os.environ["OPENAI_API_KEY"])
        else:
            # Local, dependency-light embeddings via fastembed. The model must be
            # one fastembed actually supports (the cognee default
            # openai/text-embedding-3-large is not), so set a known-good fastembed
            # model + its dimensionality explicitly (BAAI/bge-small-en-v1.5, 384).
            _try("set_embedding_provider", "fastembed")
            _try("set_embedding_model", "BAAI/bge-small-en-v1.5")
            _try("set_embedding_dimensions", 384)
    except Exception:
        pass


def _run_cognee_sync_in_background() -> None:
    """Run :func:`sync_cognee_config` off the main thread.

    ``sync_cognee_config`` performs ``import cognee``, which is heavy (several
    seconds). Calling it synchronously from an interactive handler makes the
    prompt appear to hang after a model/key change. Running it in a daemon
    thread keeps the CLI responsive; the synced config is only needed by the
    next memory operation, which happens later.
    """
    import threading
    threading.Thread(target=sync_cognee_config, daemon=True).start()


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
    # No provider is hardcoded: the model is whatever the user configured via
    # OMNI_MODEL (.env or /model). Empty means "not yet chosen" — the caller
    # prompts the user to pick one rather than silently assuming a provider.
    return os.environ.get("OMNI_MODEL", "").strip()


def pretty_model(model: str = "") -> str:
    """Turn a litellm model id into a friendly label, e.g.
    ``vertex_ai/gemini-2.5-flash`` -> ``Gemini 2.5 Flash``.
    """
    raw = (model or current_model()).strip()
    name = raw.split("/")[-1] if "/" in raw else raw
    name = name.replace("-", " ").replace("_", " ").strip()
    words = []
    for w in name.split():
        if any(c.isdigit() for c in w):
            words.append(w)
        elif len(w) <= 3:
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    label = " ".join(words) if words else raw
    return label.replace("Gpt", "GPT").replace("Oss", "OSS").replace("Ai", "AI")


# Friendly one-line descriptions for the slash-command menu. Anything not listed
# falls back to a generic label (covers MCP-provided commands).
COMMAND_DESCRIPTIONS = {
    "/clear": "Clear the screen and current conversation",
    "/compact": "Summarize then compact the conversation",
    "/init": "Create an AGENTS.md for this project",
    "/doctor": "Diagnose environment, API keys, and memory health",
    "/review": "Review staged changes or a git diff",
    "/bug": "Report a bug from inside the CLI",
    "/resume": "Resume or fork a past conversation",
    "/graph": "Build or query the codebase knowledge graph",
    "/memify": "Consolidate & improve long-term memory",
    "/improve": "Consolidate & improve long-term memory",
    "/consolidate": "Consolidate & improve long-term memory",
    "/forget": "Forget stored memories",
    "/memory": "Search the long-term memory store",
    "/index": "Index this codebase into graph memory",
    "/model": "Switch the active model",
    "/api_key": "Add an API key to your .env",
    "/cognee": "Set memory embeddings: same as the CLI (cloud) or local (offline)",
    "/autonomous": "Toggle autonomous (no-permission) mode",
    "/plan": "Create a step-by-step plan only (type 'proceed' to implement it)",
    "/developer": "Developer mode: autonomously plan, implement, and build step by step",
    "/config": "View or edit configuration",
    "/ctx_viz": "Visualize the current context window",
    "/tokens": "Show token usage for this session",
    "/cost": "Show estimated session cost",
    "/status": "Show session status",
    "/history": "View command history",
    "/commit": "Create a git commit",
    "/pr_comments": "Fetch and summarize PR comments",
    "/release_notes": "Generate release notes from git history",
    "/terminal_setup": "Configure terminal integration",
    "/pwd": "Print the working directory",
    "/ls": "List directory contents",
    "/help": "Show all commands and shortcuts",
    "?": "Show shortcuts and help",
    "exit": "Exit Omni-Dev",
    "quit": "Exit Omni-Dev",
}


def command_meta(cmd: str) -> str:
    """Return a friendly description for a slash command (with a fallback)."""
    if cmd in COMMAND_DESCRIPTIONS:
        return COMMAND_DESCRIPTIONS[cmd]
    if cmd.startswith("/"):
        return "Run the " + cmd[1:].replace("-", "_").replace("_", " ") + " command"
    return ""


def print_input_footer() -> None:
    """Print the antigravity-style status line: '? for shortcuts' on the left and
    the model on the right. Used on the Rich fallback path (the prompt_toolkit
    path shows this as a live bottom toolbar instead)."""
    try:
        width = console.size.width
    except Exception:
        width = 80
    left = "? for shortcuts"
    right = pretty_model()
    pad = max(1, width - len(left) - len(right) - 2)
    console.print(
        f"  [app.muted]{left}[/app.muted]" + " " * pad + f"[app.muted]{right}[/app.muted]"
    )


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
    """Per-turn status footer is intentionally disabled.

    Token/cost are no longer printed after every turn (the user found it noisy);
    the model name lives in the input toolbar/footer instead. Use /tokens, /cost
    or /status to see usage on demand.
    """
    return


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

        # Tool completion: a concise themed "└ result" line under the activity.
        if func_name == "__tool_result__":
            if status:
                try:
                    status.stop()
                except Exception:
                    pass
            try:
                line = format_tool_result(
                    str(args.get("tool", "")),
                    args.get("result", ""),
                    bool(args.get("is_error", False)),
                    args=args.get("args") if isinstance(args.get("args"), dict) else {},
                    console=console,
                )
                if line is not None:
                    console.print(line)
            except Exception:
                pass
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
        "/tokens", "/cost", "/status", "/model", "/api_key", "/cognee", "/memory", "/index",
        "/history", "/commit", "/pwd", "/ls", "/autonomous", "/plan", "/developer",
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

    with console.status("[status.ok]Initializing Omni-Dev and loading memories...", spinner="omni_pulse"):
        try:
            # Pin Cognee storage roots into the project (cheap, no heavy import).
            try:
                cognee_paths.prime_storage_env()
            except Exception:
                pass

            agent = OmniDevAgent()
            _apply_persisted_config(agent)
            _run_cognee_sync_in_background()
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

    # ── Durable Cognee graph recall — runs in the BACKGROUND ──────────────────
    # The graph recall is an LLM call over the knowledge graph and can take
    # seconds (cold start). Running it inline used to stall startup behind a 15s
    # timeout. Instead we show the prompt immediately and fold graph memory into
    # the system prompt when it arrives; the agent also calls `recall` itself when
    # a turn needs it, so nothing is lost by deferring.
    if startup_context:
        if agent.messages and agent.messages[0]["role"] == "system":
            agent.messages[0]["content"] += startup_context

    console.clear()
    print_banner()
    if startup_context:
        console.print("  [status.ok]Past session memories loaded into context.[/status.ok]")

    async def _bg_startup_recall():
        try:
            import cognee
            try:
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass
            res = await asyncio.wait_for(
                cognee.recall(
                    query_text="project context user preferences past work summary",
                    top_k=5,
                ),
                timeout=30,
            )
            parts = []
            for r in (res or []):
                text = None
                for attr in ("answer", "text", "content", "context", "summary",
                             "payload", "result", "graph_context", "value"):
                    v = getattr(r, attr, None)
                    if v and isinstance(v, str) and v.strip():
                        text = v
                        break
                if not text:
                    text = str(r)
                if text and text.strip() and text.strip() not in parts:
                    parts.append(text.strip())
            if parts and agent.messages and agent.messages[0]["role"] == "system":
                agent.messages[0]["content"] += (
                    "\n\n<cognee_graph_memory>\n"
                    + "\n\n".join(parts[:5])
                    + "\n</cognee_graph_memory>"
                )
        except Exception:
            pass

    try:
        asyncio.ensure_future(_bg_startup_recall())
    except Exception:
        pass

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
    # /plan stores a pending task here; typing 'proceed' executes it. Developer
    # mode makes the agent plan -> implement -> build autonomously each turn.
    plan_state = {"task": None}
    dev_mode = {"on": False}
    agent.can_use_tool = make_permission_callback(agent, ui_state)
    tool_callback = make_tool_callback(ui_state)

    # Live token streaming: render the model response token-by-token. Enabled by
    # default; set OMNI_NO_STREAM=1 to fall back to one-shot rendering. The first
    # content token stops the spinner and prints the assistant header before the
    # live view begins.
    streaming_enabled = (
        os.environ.get("OMNI_NO_STREAM", "").strip().lower()
        not in ("1", "true", "yes", "on")
    )
    if streaming_enabled:
        from src.cli import render as _render_mod

        async def _stream_render(stream):
            status = ui_state.get("status")

            def _on_first():
                if status is not None:
                    try:
                        status.stop()
                    except Exception:
                        pass
                try:
                    console.print()
                    console.print(assistant_turn_header(console=console))
                except Exception:
                    pass

            return await _render_mod.stream_response(
                stream, console, on_first_chunk=_on_first
            )

        agent.stream_render = _stream_render

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
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.styles import Style
        from prompt_toolkit.application import get_app
        import html as _html

        class SlashCommandCompleter(Completer):
            """Slash-command completer that shows each command with its
            description inline (command on the left, muted description on the
            right) — like modern agent CLIs."""

            def __init__(self, cmds):
                self.cmds = sorted(cmds)

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor.lstrip()
                if text.startswith("/"):
                    matches = [c for c in self.cmds if c.startswith(text.lower())]
                elif text.lower() in ("e", "ex", "exi", "exit", "q", "qu", "qui", "quit", "?"):
                    matches = [c for c in ("exit", "quit", "?") if c.startswith(text.lower())]
                else:
                    return
                # Align descriptions into a column.
                width = max((len(c) for c in matches), default=0) + 3
                for cmd in matches:
                    desc = command_meta(cmd)
                    pad = " " * max(2, width - len(cmd))
                    # The command carries NO explicit color (just bold) so the
                    # completion-menu base style colors it: white normally, red
                    # when selected. The description is always muted grey.
                    disp = HTML(
                        f'<b>{_html.escape(cmd)}</b>{pad}'
                        f'<desc>{_html.escape(desc)}</desc>'
                    )
                    yield Completion(cmd, start_position=-len(text), display=disp)

        # Seed prompt history so up-arrow recalls past inputs (Req 12.x).
        pt_history = InMemoryHistory()
        try:
            for item in reversed(cmd_history.get_history()):
                if item and item.strip():
                    pt_history.append_string(item)
        except Exception:
            pass

        def _bottom_toolbar():
            # Clean footer: a top rule line, then a hint (left) + model (right).
            # While the completion menu is open, show navigation hints instead.
            try:
                app = get_app()
                cols = max(20, app.output.get_size().columns)
            except Exception:
                app = None
                cols = 80

            completing = False
            try:
                completing = app is not None and app.current_buffer.complete_state is not None
            except Exception:
                completing = False

            model = pretty_model()
            if completing:
                left = "\u2191/\u2193 Navigate \u00b7 enter Select \u00b7 tab Complete \u00b7 esc Cancel"
            else:
                left = "? for shortcuts"

            pad = max(1, cols - len(left) - len(model) - 1)
            rule = "\u2500" * (cols - 1)
            return [
                ("class:tb.rule", rule + "\n"),
                ("class:tb.hint", left),
                ("class:tb.pad", " " * pad),
                ("class:tb.model", model),
            ]

        ptk_style = Style.from_dict({
            "prompt": "#E5484D bold",
            # Footer: disable the default reversed bar so it blends with the theme.
            "bottom-toolbar": "noreverse bg:default",
            "tb.rule": "#3a3f4b",
            "tb.hint": "#8A8F98",
            "tb.pad": "",
            "tb.model": "#8A8F98",
            # Slash-command menu: command WHITE by default, RED when selected,
            # with NO full-width highlight bar. 'noreverse' stops prompt_toolkit
            # from inverting the selected row (which turned the red text into a
            # red background). The description keeps its own grey style unchanged.
            "completion-menu": "bg:default",
            "completion-menu.completion": "bg:default #ffffff noreverse",
            "completion-menu.completion.current": "bg:default #E5484D bold noreverse",
            "desc": "#6b7280",
            # Hide the completion-menu scrollbar (blend into the background) —
            # the red track/button looked cheap.
            "scrollbar.background": "bg:default",
            "scrollbar.button": "bg:default",
        })

        completer = SlashCommandCompleter(commands_list)
        session = PromptSession(
            completer=completer,
            history=pt_history,
            bottom_toolbar=_bottom_toolbar,
            style=ptk_style,
            complete_while_typing=True,
        )
        use_prompt_toolkit = True
    except Exception:
        use_prompt_toolkit = False
        try:
            console.print(
                "  [status.warn]Rich input disabled (prompt_toolkit not available) — "
                "slash-command menu won't show.[/status.warn]"
            )
            console.print(
                "  [app.muted]Fix: pip install prompt_toolkit  (or re-run the installer).[/app.muted]"
            )
        except Exception:
            pass

    while True:
        try:
            console.rule(style="app.muted")
            if use_prompt_toolkit:
                # prompt_toolkit's async API runs on the main event loop. Running
                # session.prompt() inside a thread executor breaks keyboard input
                # on Windows (the win32 console reader must run on the main
                # thread), which left the prompt unable to accept typing.
                from prompt_toolkit.formatted_text import HTML as _HTML
                user_input = await session.prompt_async(_HTML('<prompt>&gt;</prompt> '))
            else:
                from rich.prompt import Prompt
                print_input_footer()
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask(" [app.accent]>[/app.accent]")
                )

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
                        description = await session.prompt_async("Describe the bug: ")
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

            # ── /graph ──
            if norm_cmd == "graph":
                parts = user_input.strip().split(" ", 1)
                sub_args = parts[1].strip() if len(parts) > 1 else ""
                console.print(f"\n[app.accent]/graph[/app.accent]")
                if sub_args.split(None, 1)[:1] == ["build"]:
                    with console.status("[app.accent]Building knowledge graph..."):
                        summary = await commands_pkg.graph_command(sub_args, console)
                else:
                    summary = await commands_pkg.graph_command(sub_args, console)
                if summary:
                    console.print(f"[app.muted]{summary}[/app.muted]")
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

            # ── /cognee (choose the embedding model for graph memory) ──
            if norm_cmd in ("cognee", "embedding", "embeddings"):
                console.print("\n[app.accent]/cognee[/app.accent]")
                prov, model, dims = cognee_paths.get_embedding_info()
                console.print(f"  [app.muted]Current memory embeddings:[/app.muted] {prov or '?'} / {model or '?'} ({dims or '?'} dims)")
                cprov, cmodel, cdims, cendpoint = cognee_paths.cloud_embedding_for_cli()
                console.print("  How should Cognee embed your memory?")
                console.print(f"    1. Same as the CLI (cloud)  — {cmodel} ({cdims}d, uses your CLI provider's account)")
                console.print("    2. Local (offline)          — fastembed BAAI/bge-small-en-v1.5 (384d, no cloud, no server)")
                choice = (await _ask_line(session, use_prompt_toolkit, "Enter choice (1-2): ")).strip()
                if choice == "1":
                    nprov, nmodel, ndims, nendpoint = cprov, cmodel, cdims, cendpoint
                elif choice == "2":
                    nprov, nmodel, ndims, nendpoint = cognee_paths.EMBEDDING_PRESETS["local"]
                else:
                    console.print("  [status.warn]Cancelled.[/status.warn]")
                    continue

                if nprov == "fastembed":
                    try:
                        import fastembed  # noqa: F401
                    except Exception:
                        console.print("  [status.warn]The local embedding engine ('fastembed') isn't installed.[/status.warn]")
                        ans = (await _ask_line(session, use_prompt_toolkit, "Install it now (one-time download)? [y/N]: ")).strip().lower()
                        if not ans.startswith("y"):
                            console.print("  [app.muted]Cancelled. Install manually:  pip install fastembed[/app.muted]")
                            continue
                        import sys as _sys
                        import subprocess as _sp
                        with console.status("[app.accent]Installing fastembed (this can take a minute)...", spinner="omni_pulse"):
                            _sp.run([_sys.executable, "-m", "pip", "install", "fastembed"],
                                    capture_output=True, text=True)
                        try:
                            import fastembed  # noqa: F401
                            console.print("  [status.ok]fastembed installed.[/status.ok]")
                        except Exception:
                            console.print("  [status.warn]Install failed. Try manually:  pip install fastembed[/status.warn]")
                            continue

                changed = (str(ndims) != str(dims)) if dims else True
                if changed:
                    console.print(f"  [status.warn]Embedding dimensions change ({dims or '?'} -> {ndims}).[/status.warn]")
                    console.print("  [status.warn]Existing graph-memory vectors won't match and must be rebuilt.[/status.warn]")
                    ans = (await _ask_line(session, use_prompt_toolkit, "Back up & reset the memory vector store now? [y/N]: ")).strip().lower()
                    if ans.startswith("y"):
                        dest = cognee_paths.backup_databases(label=f"dim{dims or 'x'}")
                        if dest:
                            console.print(f"  [status.ok]Backed up old store -> {os.path.basename(dest)}[/status.ok]")
                        else:
                            console.print("  [app.muted]No existing store to back up.[/app.muted]")

                cognee_paths.set_embedding(nprov, nmodel, ndims, nendpoint)
                where = "cloud (same as CLI)" if choice == "1" else "local (offline)"
                console.print(f"  [status.ok]Cognee embeddings set to {where}:[/status.ok] {nprov} / {nmodel} ({ndims} dims)")
                console.print("  [app.muted]Restart Omni-Dev for it to take effect, then run /index to rebuild memory.[/app.muted]")
                console.print("  [app.muted]Facts saved via 'remember' (offline store) are unaffected.[/app.muted]")
                continue

            # ── /index ──
            if norm_cmd == "index":
                import glob
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                # Extensions worth ingesting as text. Everything else (binaries,
                # images, lockfiles) is skipped so cognify stays fast and useful.
                TEXT_EXT = {
                    ".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json",
                    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".html", ".css",
                    ".scss", ".java", ".go", ".rs", ".c", ".cc", ".cpp", ".h",
                    ".hpp", ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".sql",
                    ".kt", ".swift", ".scala", ".vue", ".svelte", ".r", ".jl",
                    ".gradle", ".dockerfile", ".env.example",
                }
                MAX_FILE_BYTES = 60_000      # skip huge files (per-file cap)
                MAX_TOTAL_FILES = 400        # bound cognify cost/time
                MAX_TOTAL_BYTES = 2_500_000  # ~2.5 MB of text total
                SKIP = ("node_modules", ".git", "venv", "__pycache__",
                        ".cognee_data", "dist", "build", ".next", ".pytest_cache")
                docs = []
                files_added = 0
                total_bytes = 0
                with console.status("[app.accent]Reading & ingesting file contents into Cognee Graph Memory...", spinner="omni_pulse"):
                    import cognee
                    for filepath in glob.glob(os.path.join(".", "**", "*.*"), recursive=True):
                        if any(x in filepath for x in SKIP):
                            continue
                        ext = os.path.splitext(filepath)[1].lower()
                        if ext not in TEXT_EXT:
                            continue
                        try:
                            if os.path.getsize(filepath) > MAX_FILE_BYTES:
                                continue
                            with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                                content = fh.read()
                        except Exception:
                            continue
                        if not content.strip():
                            continue
                        # Each file becomes its own document so Cognee extracts
                        # per-file entities/relationships into the graph. The path
                        # header lets recall attribute facts back to a file.
                        doc = f"Source file: {filepath}\n\n{content}"
                        docs.append(doc)
                        files_added += 1
                        total_bytes += len(content)
                        if files_added >= MAX_TOTAL_FILES or total_bytes >= MAX_TOTAL_BYTES:
                            break
                    if docs:
                        try:
                            # Ingest CONTENT (not just paths) and build the graph so
                            # the knowledge survives even if the repo is deleted.
                            await cognee.add(docs, dataset_name="codebase_architecture")
                            await cognee.cognify()
                        except Exception:
                            # Fallback to the lifecycle API if add/cognify is unavailable.
                            try:
                                await cognee.remember(
                                    "\n\n".join(docs[:50]),
                                    dataset_name="codebase_architecture",
                                )
                            except Exception:
                                pass
                if files_added:
                    console.print(
                        f"  [status.ok]{files_added} files ({total_bytes // 1024} KB of code) "
                        f"ingested into Cognee Graph Memory — durable across sessions.[/status.ok]"
                    )
                else:
                    console.print("  [status.warn]No text/code files found to index here.[/status.warn]")
                continue


            # ── /memory ──
            # ── /forget (Cognee forget lifecycle) ──
            if norm_cmd == "forget":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                parts = user_input.strip().split()
                # parts[0] is the command itself; the rest are args.
                args = parts[1:] if len(parts) > 1 else []
                scope = (args[0].lower() if args else "memory")
                dataset_name = ""
                if scope == "dataset":
                    dataset_name = args[1] if len(args) > 1 else ""
                    if not dataset_name:
                        console.print("  [status.warn]Usage: /forget dataset <name>[/status.warn]")
                        continue
                elif scope not in ("memory", "all"):
                    # Treat an unknown token as a dataset name.
                    dataset_name = scope
                    scope = "dataset"

                with console.status("[app.accent]Forgetting memories...", spinner="omni_pulse"):
                    msg = "the memory layer"
                    try:
                        import cognee
                        try:
                            cognee_paths.configure_cognee_storage()
                        except Exception:
                            pass
                        if scope == "dataset":
                            await cognee.forget(dataset=dataset_name)
                            msg = f"dataset '{dataset_name}'"
                        elif scope == "all":
                            await cognee.forget(everything=True)
                            try:
                                from src.simple_memory import clear_all as _sm_clear
                                _sm_clear()
                            except Exception:
                                pass
                            msg = "all memories (local store cleared)"
                        else:
                            await cognee.forget(memory_only=True, dataset="user_memory")
                            msg = "the memory layer"
                    except Exception as e:
                        # Honor 'all' offline even if Cognee fails.
                        if scope == "all":
                            try:
                                from src.simple_memory import clear_all as _sm_clear
                                _sm_clear()
                                msg = "all memories (local store cleared; Cognee unavailable)"
                            except Exception:
                                msg = f"nothing — error: {e}"
                        else:
                            msg = f"nothing — error: {e}"
                console.print(f"  [status.ok]🧹 Forgot {msg}.[/status.ok]")
                continue

            # ── /memify or /improve or /consolidate (Cognee memify + improve) ──
            if norm_cmd in ("memify", "improve", "consolidate"):
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                with console.status("[app.accent]Consolidating & improving long-term memory...", spinner="omni_pulse"):
                    ok = False
                    try:
                        import cognee
                        try:
                            cognee_paths.configure_cognee_storage()
                        except Exception:
                            pass
                        try:
                            await cognee.memify()
                        except Exception:
                            pass
                        try:
                            await cognee.improve(run_in_background=True)
                        except Exception:
                            pass
                        ok = True
                    except Exception:
                        ok = False
                if ok:
                    console.print("  [status.ok]🧠 Memory consolidated.[/status.ok]")
                else:
                    console.print("  [status.warn]Memory consolidation unavailable (Cognee error). Local memory unaffected.[/status.warn]")
                continue

            if norm_cmd == "memory":
                console.print(f"\n[app.accent]{cmd}[/app.accent]")
                if use_prompt_toolkit:
                    query = await session.prompt_async("What memory to recall? (or Enter for recent) ")
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

            # ── /developer (toggle autonomous plan->implement->build mode) ──
            if norm_cmd in ("developer", "dev"):
                dev_mode["on"] = not dev_mode["on"]
                if dev_mode["on"]:
                    agent.autonomous = True
                    console.print("\n[status.ok]Developer mode ON[/status.ok]")
                    console.print("  [app.muted]I'll plan, implement, and build step by step, autonomously, each turn.[/app.muted]")
                else:
                    agent.autonomous = os.environ.get("OMNI_AUTONOMOUS", "false").strip().lower() in ("1", "true", "yes", "on")
                    console.print("\n[status.warn]Developer mode OFF[/status.warn]")
                continue

            # ── /plan (produce a plan only; 'proceed' implements it) ──
            if norm_cmd == "plan":
                parts = user_input.strip().split(" ", 1)
                task = parts[1].strip() if len(parts) > 1 else ""
                console.print("\n[app.accent]/plan[/app.accent]")
                if not task:
                    task = (await _ask_line(session, use_prompt_toolkit, "What should I plan? ")).strip()
                if not task:
                    console.print("  [status.warn]No task provided.[/status.warn]")
                    continue
                plan_prompt = (
                    "Create a concise, numbered step-by-step IMPLEMENTATION PLAN for the task below. "
                    "Output ONLY the plan as markdown. Do NOT modify files, run commands, or use mutating tools yet — "
                    "this is planning only.\n\n"
                    f"TASK: {task}"
                )
                console.print()
                console.print(user_turn_header(console=console))
                console.print(gutter_line(user_input, role="user", console=console))
                console.print(turn_separator("user", console=console))
                final_response = await run_agent_task(agent, plan_prompt, tool_callback, ui_state)
                render_task_result(agent, final_response)
                plan_state["task"] = task
                console.print("  [app.muted]Type 'proceed' to implement this plan, or refine and /plan again.[/app.muted]")
                continue

            # ── proceed (execute the pending /plan) ──
            if cmd_lower in ("proceed", "/proceed", "go", "go ahead", "yes proceed", "do it") and plan_state.get("task"):
                task = plan_state["task"]
                plan_state["task"] = None
                console.print("\n[app.accent]Proceeding with the plan...[/app.accent]")
                exec_prompt = (
                    "Implement the following task fully, following the plan you just produced. "
                    "Build and verify it, fixing any errors as you go.\n\n"
                    f"TASK: {task}"
                )
                final_response = await run_agent_task(agent, exec_prompt, tool_callback, ui_state)
                render_task_result(agent, final_response)
                continue

            # ── Regular agent task ──
            # Require an explicitly chosen model — no provider is hardcoded.
            if not current_model():
                console.print(
                    "  [status.warn]No model selected.[/status.warn] Run "
                    "[app.accent]/model[/app.accent] to choose one (any LiteLLM model "
                    "string), or set [app.accent]OMNI_MODEL[/app.accent] in your .env, then retry."
                )
                continue
            from src.simple_memory import recall as _sm_recall
            past_context = ""
            try:
                sm_parts = _sm_recall(user_input, top_k=5)
                if sm_parts:
                    past_context = "\n\n<memory_context>\n" + "\n\n".join(sm_parts[:5]) + "\n</memory_context>"
            except Exception:
                pass
            augmented_prompt = f"{user_input}{past_context}"
            if dev_mode["on"]:
                augmented_prompt = (
                    "DEVELOPER MODE: Work fully autonomously. First outline a brief step-by-step plan, then "
                    "implement it step by step — writing code, running builds/tests, and fixing errors as you go — "
                    "until the task is fully complete and verified.\n\n"
                    + augmented_prompt
                )

            # Echo the user turn (themed framing).
            console.print()
            console.print(user_turn_header(console=console))
            console.print(gutter_line(user_input, role="user", console=console))
            console.print(turn_separator("user", console=console))

            final_response = await run_agent_task(agent, augmented_prompt, tool_callback, ui_state)

            # Journaling (SimpleMemory + best-effort background Cognee).
            # Skip error/notice responses (API-key errors, interrupts, config
            # errors, empty-response notices) so failures never pollute memory and
            # resurface as confusing context on later sessions.
            from src.simple_memory import remember as sm_remember

            def _is_journalable(resp: str) -> bool:
                if not resp or not resp.strip():
                    return False
                low = resp.lower()
                markers = (
                    "\U0001f6a8", "\u26a0",  # 🚨 ⚠️
                    "api key", "api_key", "authenticationerror",
                    "configuration error", "connectivity error",
                    "empty response", "interrupted", "error during task",
                )
                return not any(m in low for m in markers)

            journalable = _is_journalable(final_response)
            if journalable:
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
            if journalable:
                asyncio.ensure_future(_bg_cognee_journal())

            # Render the final assistant response.
            console.print()
            if not final_response or not final_response.strip():
                console.print(assistant_turn_header(console=console))
                console.print("  [status.warn]The model returned an empty response.[/status.warn]")
                console.print("  [app.muted]Try /model to switch providers, or /doctor to check your API key.[/app.muted]")
            elif getattr(agent, "_final_was_streamed", False):
                # Already rendered live token-by-token by the stream hook; just
                # close the turn so we don't double-render the answer.
                console.print(turn_separator("assistant", console=console))
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

def _safe_set_env_key(key: str, value: str) -> None:
    """Safely update .env without using dotenv.set_key directly if unwritable.
    
    Python's tempfile.NamedTemporaryFile (used by dotenv on Windows) contains a bug 
    where it infinitely loops trying to create a file if os.access passes but 
    os.open throws PermissionError (e.g. in C:\WINDOWS\System32).
    By testing open() first, we bypass the bug and fail instantly instead of hanging.
    """
    try:
        with open(".env", "a", encoding="utf-8"):
            pass
    except Exception:
        return

    try:
        from dotenv import set_key
        set_key(".env", key, value)
    except Exception:
        pass

def render_task_result(agent, final_response):
    """Render a task result like the main REPL turn: skip a second render if the
    stream hook already showed it, else render the markdown."""
    console.print()
    if not final_response or not final_response.strip():
        console.print(assistant_turn_header(console=console))
        console.print("  [status.warn]The model returned an empty response.[/status.warn]")
    elif getattr(agent, "_final_was_streamed", False):
        console.print(turn_separator("assistant", console=console))
    else:
        console.print(assistant_turn_header(console=console))
        render_final(final_response, console)
        console.print(turn_separator("assistant", console=console))


async def run_agent_task(agent, prompt, tool_callback, ui_state):
    """Run the agent with an active spinner; Ctrl+C interrupts cleanly (Req 1.7, 7.11).

    The task is run as an ``asyncio`` future with an ``abort_event``. On
    ``KeyboardInterrupt`` the event is set and the task is awaited again so the
    loop stops at the next checkpoint and history stays consistent — the REPL is
    never killed (Req 7.12).
    """
    status = console.status("[app.accent]Thinking[/app.accent]", spinner="omni_pulse", spinner_style="app.accent")
    try:
        status.start()
    except Exception:
        pass
    ui_state["status"] = status
    # Register the spinner with the shared coordinator so mid-task input prompts
    # (e.g. the ask_user tool) can pause it while waiting for the user.
    try:
        from src.cli import ui_state as ui_coord
        ui_coord.set_active_status(status)
    except Exception:
        ui_coord = None

    # ── Live status animation: one randomly-chosen verb + elapsed time + hint ──
    # Mirrors Claude Code's spinner: pick a single whimsical verb per turn (not a
    # rotating list) so the wait indicator feels alive but calm. Cancelled in finally.
    import random as _random
    _verb = _random.choice(THINKING_VERBS)

    async def _animate():
        import time as _t
        start = _t.monotonic()
        try:
            while True:
                elapsed = int(_t.monotonic() - start)
                status.update(
                    f"[app.accent]{_verb}\u2026[/app.accent]"
                    f"[app.muted] · {elapsed}s · ctrl+c to interrupt[/app.muted]"
                )
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return
        except Exception:
            return

    animator = asyncio.ensure_future(_animate())

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
        try:
            animator.cancel()
        except Exception:
            pass
        ui_state["status"] = None
        if ui_coord is not None:
            try:
                ui_coord.clear_active_status()
            except Exception:
                pass
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
            choice = await session.prompt_async(prompt_str)
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
# Shared interactive line reader.
#
# CRITICAL (Windows): the win32 console reader prompt_toolkit installs must run
# on the main event-loop thread. Reading sub-prompts via rich.Prompt.ask inside
# run_in_executor (a worker thread) corrupts that reader, so the FIRST /model or
# /api_key works but every prompt afterwards stops accepting input. Always reuse
# the active prompt_toolkit session (as /memory and /resume already do).
# ─────────────────────────────────────────────────────────────────────────────
async def _ask_line(session, use_prompt_toolkit, prompt_text, is_password=False):
    if use_prompt_toolkit:
        try:
            from prompt_toolkit import PromptSession
            # Use a fresh session to avoid completer/toolbar interference
            temp_session = PromptSession()
            ans = await temp_session.prompt_async(prompt_text, is_password=is_password)
            return (ans or "").strip()
        except Exception:
            pass
            
    from rich.prompt import Prompt as RPrompt
    label = prompt_text.rstrip(": ").strip() or prompt_text
    ans = await asyncio.get_event_loop().run_in_executor(
        None, lambda: RPrompt.ask(label, password=is_password)
    )
    return (ans or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# /model handler (routes every choice through the Model Router) — Req 5.2
# ─────────────────────────────────────────────────────────────────────────────
async def handle_model_command(user_input, session, use_prompt_toolkit):
    parts = user_input.strip().split(" ", 1)
    console.print("\n[app.accent]/model[/app.accent]")
    if len(parts) == 2:
        new_model = parts[1].strip()
    else:
        console.print("  Enter the model you want to use, in LiteLLM format.")
        console.print("  [app.muted]Format: <provider>/<model>  (or a bare OpenAI/Anthropic model name)[/app.muted]")
        console.print("  [app.muted]Examples:[/app.muted]")
        console.print("    [app.muted]gemini/gemini-1.5-pro         - Google AI Studio[/app.muted]")
        console.print("    [app.muted]vertex_ai/gemini-2.5-flash    - Google Vertex AI[/app.muted]")
        console.print("    [app.muted]gpt-4o                        - OpenAI[/app.muted]")
        console.print("    [app.muted]claude-3-5-sonnet-20241022    - Anthropic[/app.muted]")
        console.print("    [app.muted]groq/llama-3.3-70b-versatile  - Groq[/app.muted]")
        console.print("    [app.muted]ollama/llama3.3               - local Ollama[/app.muted]")
        console.print("  [app.muted]Full provider list: https://docs.litellm.ai/docs/providers[/app.muted]")
        new_model = await _ask_line(session, use_prompt_toolkit, "Enter model string: ")

    if not new_model:
        console.print("  [status.warn]No model provided.[/status.warn]")
        return

    # Single authoritative normalization (Req 5.2).
    canonical = model_router.normalize_model(new_model)
    if not canonical:
        console.print("  [status.warn]Could not parse model name.[/status.warn]")
        return

    os.environ["OMNI_MODEL"] = canonical
    _safe_set_env_key("OMNI_MODEL", canonical)

    decision = model_router.route(canonical, os.environ)

    # Cloud Ollama: point the endpoint at ollama.com so routing/keys line up.
    if decision.is_cloud_ollama:
        base = os.environ.get("OLLAMA_API_BASE")
        if not base or base == "http://localhost:11434":
            os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
            _safe_set_env_key("OLLAMA_API_BASE", "https://ollama.com")
            decision = model_router.route(canonical, os.environ)

    console.print(f"  [status.ok]Model switched to:[/status.ok] {decision.canonical_model}")
    # Sync Cognee's LLM config off the main thread so the heavy `import cognee`
    # never blocks the prompt after a model switch.
    _run_cognee_sync_in_background()
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
        choice = await _ask_line(session, use_prompt_toolkit, "Enter choice (1-11): ")
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
            provider_key = await _ask_line(session, use_prompt_toolkit, "Enter env var name: ")
            provider_key = provider_key.upper()
            if "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key and not provider_key.endswith("_API_KEY"):
                provider_key += "_API_KEY"

        is_secret = "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key
        key_value = await _ask_line(session, use_prompt_toolkit, f"Enter {provider_key}: ", is_password=is_secret)

    if key_value:
        os.environ[provider_key] = key_value
        if provider_key == "OLLAMA_API_KEY":
            current = os.environ.get("OMNI_MODEL", "").lower()
            if "cloud" in current and "ollama" in current:
                if not os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_API_BASE") == "http://localhost:11434":
                    os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
                    _safe_set_env_key("OLLAMA_API_BASE", "https://ollama.com")
        _safe_set_env_key(provider_key, key_value)
        _run_cognee_sync_in_background()
        console.print(f"  [status.ok]API key saved:[/status.ok] {provider_key}")


if __name__ == "__main__":
    asyncio.run(main())
