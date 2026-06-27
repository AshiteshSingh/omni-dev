"""
interface.py - Enhanced Interactive CLI Interface

Sleek, modern interactive interface inspired by Antigravity CLI / Claude Code.
Features auto-completion, live bottom status bar, and clean visual hierarchy.
PRESERVED: Cognee memory integration (cognee.add/cognify/search)
"""
import sys
import io
import os

# Windows UTF-8 fix: must happen before Rich is imported
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "buffer"):
        enc = getattr(sys.stdout, "encoding", "") or ""
        if enc.lower() not in ("utf-8", "utf8"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        enc = getattr(sys.stderr, "encoding", "") or ""
        if enc.lower() not in ("utf-8", "utf8"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import asyncio
import subprocess
import warnings
from dotenv import load_dotenv

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

warnings.filterwarnings("ignore", category=UserWarning)
import logging
logging.getLogger("cognee").setLevel(logging.CRITICAL)

from src.agent.core import OmniDevAgent

load_dotenv()
if not os.environ.get("OLLAMA_API_BASE") and os.environ.get("OLLAMA_API_KEY"):
    os.environ["OLLAMA_API_BASE"] = "https://ollama.com"

def sync_cognee_config():
    """Sync configuration dynamically into Cognee based on OMNI_MODEL and env vars."""
    try:
        import os
        import cognee
        
        model_name = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro").strip()
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
            elif prov_prefix == "ollama":
                provider = "ollama"
                model = model_part
                api_key = os.environ.get("OLLAMA_API_KEY", "")
                endpoint = os.environ.get("OLLAMA_API_BASE")
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
            
        # Set default Embeddings to local fastembed to ensure it works for free if OpenAI key is not available
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

# Rich Console: force_terminal bypasses Windows legacy renderer
console = Console(
    highlight=False,
    force_terminal=True,
    legacy_windows=False,
)


def print_banner(agent: OmniDevAgent):
    """Prints the sleek startup banner inspired by Antigravity CLI."""
    model = os.environ.get("OMNI_MODEL", "groq/openai/gpt-oss-120b").strip()
    try:
        branch = subprocess.check_output(
            "git branch --show-current",
            shell=True, text=True, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        ).strip() or "No Git"
    except Exception:
        branch = "No Git"

    console.print()
    console.print("  [bold cyan]/\\\\[/bold cyan]     [bold blue]Omni-Dev CLI 2.0.0[/bold blue]")
    console.print(" [bold cyan]/  \\\\[/bold cyan]    [dim]Context-Aware Agentic Coding (Cognee Graph Memory)[/dim]")
    console.print(f"[bold cyan]/____\\\\[/bold cyan]   [bold yellow]{model}[/bold yellow]  |  [green]git: {branch}[/green]")
    console.print("         [dim]~[/dim]")
    console.print()


async def main():
    console.clear()

    with console.status("[bold green]Initializing Omni-Dev and loading memories..."):
        try:
            agent = OmniDevAgent()
            sync_cognee_config()
        except Exception as e:
            console.print(f"[bold red]Failed to initialize agent:[/bold red] {e}")
            return

    print_banner(agent)

    # Setup interactive PromptSession with autocomplete & live status bar
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.styles import Style

        commands_list = [
            "/help", "/tokens", "/cost", "/model", "/api_key", "/init", "/doctor",
            "/review", "/ctx_viz", "/config", "/compact", "/memory", "/index",
            "/history", "/commit", "/pwd", "/ls", "/clear", "/autonomous", "exit", "quit", "?"
        ]

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

        completer = SlashCommandCompleter(commands_list)

        style = Style.from_dict({
            'bottom-toolbar': 'bg:#1e1e1e #cccccc',
        })

        def get_bottom_toolbar():
            model = os.environ.get("OMNI_MODEL", "groq/openai/gpt-oss-120b").strip()
            from src.cost_tracker import get_tracker
            tok = get_tracker().total_tokens
            cost = get_tracker().total_cost_usd
            auto_mode = os.environ.get("OMNI_AUTONOMOUS", "false").lower() == "true"
            auto_str = ' <style color="#ff3333"><b>[AUTONOMOUS]</b></style>' if auto_mode else ''
            return HTML(f'<b>? for shortcuts</b> <style color="#555555">|</style> Type <b>/</b> to complete <style color="#555555">|</style> Model: <style color="yellow"><b>{model}</b></style>{auto_str} <style color="#555555">|</style> <style color="magenta"><b>{tok:,} tok (${cost:.4f})</b></style>')

        session = PromptSession(completer=completer, style=style, bottom_toolbar=get_bottom_toolbar)
        use_prompt_toolkit = True
    except Exception:
        use_prompt_toolkit = False

    while True:
        try:
            console.rule(style="dim")
            if use_prompt_toolkit:
                # Run prompt_toolkit asynchronously inside asyncio loop
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: session.prompt("> ")
                )
            else:
                from rich.prompt import Prompt
                user_input = Prompt.ask(" [bold cyan]>[/bold cyan]")

            cmd = user_input.strip()
            cmd_lower = cmd.lower()

            # Exit
            if cmd_lower in ["exit", "quit"]:
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                console.print("  [dim]└[/dim] [italic]Shutting down Omni-Dev. Goodbye![/italic]\n")
                break

            # /help or ?
            if cmd_lower in ["/help", "?"]:
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                console.print("  [dim]└[/dim] [green]Showing available commands & shortcuts[/green]\n")
                table = Table(title="Available Commands", border_style="dim", show_header=True, header_style="bold blue")
                table.add_column("Command", style="bold cyan", no_wrap=True)
                table.add_column("Description", style="white")
                commands_info = [
                    ("/help / ?", "Show this help menu and shortcuts"),
                    ("/tokens / /cost", "View live session token usage and cost breakdown"),
                    ("/model [name]", "Switch LLM provider (e.g., groq/openai/gpt-oss-120b)"),
                    ("/api_key [prov] [key]", "Add an API key securely to .env"),
                    ("/init", "Analyze codebase -> create AGENTS.md project instructions"),
                    ("/doctor", "Diagnose environment: API keys, tools, dependencies"),
                    ("/review [ref]", "AI code review of git diff (e.g., /review HEAD~1)"),
                    ("/ctx_viz", "Visualize conversation context & memory state"),
                    ("/config [key] [val]", "View/set configuration values"),
                    ("/compact", "AI-summarize conversation then clear (keeps Cognee memory)"),
                    ("/memory", "Query Cognee Knowledge Graph directly"),
                    ("/index", "Crawl codebase and push to Cognee Graph Memory"),
                    ("/history", "View agent internal message history"),
                    ("/commit [msg]", "Create a Git commit"),
                    ("/pwd", "Print current working directory"),
                    ("/ls", "List files in current directory"),
                    ("/clear", "Clear the terminal window"),
                    ("/autonomous", "Toggle autonomous mode (skip security confirmation prompts for all commands)"),
                    ("exit / quit", "Exit Omni-Dev"),
                ]
                for cmd_name, desc in commands_info:
                    table.add_row(cmd_name, desc)
                console.print(table)
                continue

            # /clear
            if cmd_lower == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
                print_banner(agent)
                continue

            # /autonomous
            if cmd_lower == "/autonomous":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                current_state = os.environ.get("OMNI_AUTONOMOUS", "false").lower() == "true"
                new_state = not current_state
                os.environ["OMNI_AUTONOMOUS"] = "true" if new_state else "false"
                try:
                    from dotenv import set_key
                    set_key(".env", "OMNI_AUTONOMOUS", "true" if new_state else "false")
                except Exception:
                    pass
                status_str = "[bold green]ENABLED[/bold green] (security prompts disabled)" if new_state else "[bold yellow]DISABLED[/bold yellow] (security prompts active)"
                console.print(f"  [dim]└[/dim] Autonomous Mode is now {status_str}.")
                continue

            # /pwd
            if cmd_lower == "/pwd":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                console.print(f"  [dim]└[/dim] {os.getcwd()}")
                continue

            # /ls
            if cmd_lower == "/ls":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                console.print("  [dim]└[/dim] Listing directory contents:\n")
                os.system("dir" if os.name == "nt" else "ls -la")
                continue

            # /cost or /tokens or /status
            if cmd_lower in ["/cost", "/tokens", "/status"]:
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                from src.cost_tracker import get_tracker
                console.print("  [dim]└[/dim] [green]Live Session Usage:[/green]")
                console.print(Panel(
                    get_tracker().get_summary(),
                    title="[bold magenta]Token & Cost Breakdown[/bold magenta]",
                    border_style="magenta",
                ))
                continue

            # /history
            if cmd_lower == "/history":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                console.print("  [dim]└[/dim] Agent Message History:\n")
                for i, msg in enumerate(agent.messages):
                    role = msg.get("role", "?").upper()
                    content = str(msg.get("content", ""))[:150]
                    if len(str(msg.get("content", ""))) > 150:
                        content += "..."
                    tool_calls = msg.get("tool_calls", [])
                    tc_str = f" [{len(tool_calls)} tool calls]" if tool_calls else ""
                    console.print(f"  [dim][{i}][/dim] [bold]{role}[/bold]{tc_str}: {content}")
                continue

            # /commit
            if cmd_lower.startswith("/commit"):
                parts = user_input.strip().split(" ", 1)
                console.print(f"\n[bold cyan]{parts[0]}[/bold cyan]")
                if len(parts) == 2:
                    msg = parts[1].strip()
                    os.system(f'git add -A && git commit -m "{msg}"')
                    console.print(f"  [dim]└[/dim] [bold green]Commit created: '{msg}'[/bold green]")
                else:
                    console.print("  [dim]└[/dim] [yellow]Usage: /commit <message>[/yellow]")
                continue

            # /compact
            if cmd_lower == "/compact":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                model = os.environ.get("OMNI_MODEL", "groq/openai/gpt-oss-120b")
                with console.status("  [dim]└[/dim] [bold magenta]AI is summarizing conversation before compacting..."):
                    from src.commands.compact import compact_command
                    summary, new_messages = await compact_command(agent.messages, model)
                agent.messages = new_messages
                from src.context import invalidate_context_cache
                invalidate_context_cache()
                agent._context = {}
                console.print("  [dim]└[/dim] [bold green]Session compacted successfully![/bold green]")
                if summary and not summary.startswith("Error"):
                    console.print(Panel(Markdown(summary[:1500]), title="[bold cyan]Saved Summary[/bold cyan]", border_style="cyan"))
                continue

            # /init
            if cmd_lower == "/init":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                with console.status("  [dim]└[/dim] [bold magenta]Analyzing codebase..."):
                    from src.commands.init_cmd import init_command
                    init_prompt = await init_command()
                console.print("  [dim]└[/dim] [bold cyan]Agent is creating AGENTS.md...[/bold cyan]\n")
                with console.status("  [dim]└[/dim] [bold green]Writing AGENTS.md..."):
                    response = await agent.execute_task(init_prompt)
                console.print(Panel(Markdown(response), title="[bold cyan]/init Result[/bold cyan]", border_style="cyan"))
                continue

            # /doctor
            if cmd_lower == "/doctor":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                with console.status("  [dim]└[/dim] [bold magenta]Running diagnostics..."):
                    from src.commands.doctor import doctor_command
                    report = await doctor_command()
                console.print(Panel(Markdown(report), title="[bold yellow]Doctor Report[/bold yellow]", border_style="yellow"))
                continue

            # /review
            if cmd_lower.startswith("/review"):
                parts = user_input.strip().split(" ", 1)
                target = parts[1].strip() if len(parts) > 1 else "HEAD"
                console.print(f"\n[bold cyan]/review {target}[/bold cyan]")
                with console.status(f"  [dim]└[/dim] [bold magenta]Getting git diff for {target}..."):
                    from src.commands.review import review_command
                    review_prompt = await review_command(target)
                if review_prompt.startswith("Error") or review_prompt.startswith("No changes"):
                    console.print(f"  [dim]└[/dim] [yellow]{review_prompt}[/yellow]")
                    continue
                console.print("  [dim]└[/dim] [bold cyan]Reviewing code changes...[/bold cyan]\n")
                with console.status("  [dim]└[/dim] [bold green]AI is reviewing your code..."):
                    response = await agent.execute_task(review_prompt)
                console.print(Panel(Markdown(response), title="[bold cyan]Code Review[/bold cyan]", border_style="cyan"))
                continue

            # /ctx_viz
            if cmd_lower == "/ctx_viz":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                from src.commands.ctx_viz import ctx_viz_command
                report = await ctx_viz_command(agent.messages, agent._context)
                console.print(Panel(Markdown(report), title="[bold blue]Context Visualization[/bold blue]", border_style="blue"))
                continue

            # /config
            if cmd_lower.startswith("/config"):
                parts = user_input.strip().split(" ", 2)
                key = parts[1].strip() if len(parts) > 1 else None
                value = parts[2].strip() if len(parts) > 2 else None
                console.print(f"\n[bold cyan]{parts[0]}[/bold cyan]")
                from src.commands.config_cmd import config_command
                result = await config_command(key, value)
                console.print("  [dim]└[/dim] " + result)
                continue

            # /model
            if cmd_lower.startswith("/model"):
                parts = user_input.strip().split(" ", 1)
                console.print("\n[bold cyan]/model[/bold cyan]")
                if len(parts) == 2:
                    new_model = parts[1].strip()
                else:
                    console.print("  [dim]└[/dim] Select an LLM Provider/Model:")
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
                    choice = choice.strip()
                    model_map = {
                        "1": "groq/openai/gpt-oss-120b",
                        "2": "groq/llama-3.3-70b-versatile",
                        "3": "gpt-4o",
                        "4": "claude-3-5-sonnet-20241022",
                        "5": "gemini/gemini-1.5-pro",
                        "6": "vertex_ai/gemini-1.5-pro",
                        "7": "openrouter/anthropic/claude-3.5-sonnet",
                        "8": "openrouter/google/gemini-pro-1.5",
                        "9": "ollama/llama3",
                    }
                    if choice in model_map:
                        new_model = model_map[choice]
                    else:
                        new_model = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask("Enter litellm model string"))
                        new_model = new_model.strip()

                if new_model:
                    if new_model.lower().startswith("model "):
                        new_model = new_model[6:].strip()
                    elif new_model.lower().startswith("models/"):
                        new_model = new_model[7:].strip()
                    elif new_model.lower().startswith("ollama "):
                        new_model = "ollama/" + new_model[7:].strip()

                    known_providers = ("groq/", "openai/", "anthropic/", "gemini/", "vertex_ai/", "openrouter/", "ollama/", "mistral/", "deepseek/", "huggingface/", "azure/", "cohere/")
                    if not any(new_model.lower().startswith(p) for p in known_providers):
                        lower_m = new_model.lower()
                        if "/" in new_model:
                            new_model = "openrouter/" + new_model
                        elif "oss" in lower_m or any(k in lower_m for k in ["llama", "mixtral", "gemma", "whisper"]):
                            new_model = "groq/" + new_model
                        elif "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
                            new_model = "openai/" + new_model
                        elif "claude" in lower_m:
                            new_model = "anthropic/" + new_model
                        elif "gemini" in lower_m:
                            new_model = "gemini/" + new_model
                        elif any(k in lower_m for k in ["glm", "qwen", "deepseek", "phi", "yi"]):
                            new_model = "openrouter/" + new_model

                    os.environ["OMNI_MODEL"] = new_model
                    try:
                        from dotenv import set_key
                        set_key(".env", "OMNI_MODEL", new_model)
                    except Exception:
                        pass
                    sync_cognee_config()
                    console.print(f"  [dim]└[/dim] [bold green]Model switched to:[/bold green] {new_model}")
                continue

            # /api_key
            if cmd_lower.startswith("/api_key"):
                parts = user_input.strip().split(" ", 2)
                console.print("\n[bold cyan]/api_key[/bold cyan]")
                if len(parts) == 3:
                    provider_key = parts[1].strip().upper()
                    if not provider_key.endswith("_API_KEY"):
                        provider_key += "_API_KEY"
                    key_value = parts[2].strip()
                else:
                    console.print("  [dim]└[/dim] Select API Provider:")
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
                    choice = choice.strip()
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
                        provider_key = provider_key.strip().upper()
                        if "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key and not provider_key.endswith("_API_KEY"):
                            provider_key += "_API_KEY"

                    is_secret = "PROJECT" not in provider_key and "LOCATION" not in provider_key and "BASE" not in provider_key
                    key_value = await asyncio.get_event_loop().run_in_executor(None, lambda: RPrompt.ask(f"Enter {provider_key}", password=is_secret))
                    key_value = key_value.strip()

                if key_value:
                    os.environ[provider_key] = key_value
                    if provider_key == "OLLAMA_API_KEY" and not os.environ.get("OLLAMA_API_BASE"):
                        os.environ["OLLAMA_API_BASE"] = "https://ollama.com"
                    try:
                        from dotenv import set_key
                        set_key(".env", provider_key, key_value)
                    except Exception:
                        pass
                    sync_cognee_config()
                    console.print(f"  [dim]└[/dim] [bold green]API key saved:[/bold green] {provider_key}")
                continue

            # /index
            if cmd_lower == "/index":
                import glob
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                with console.status("  [dim]└[/dim] [bold magenta]Indexing codebase into Cognee Graph Memory..."):
                    import cognee
                    files_added = 0
                    for filepath in glob.glob(os.path.join(".", "**", "*.*"), recursive=True):
                        if any(x in filepath for x in ["node_modules", ".git", "venv", "__pycache__"]):
                            continue
                        try:
                            await cognee.add(f"Codebase File: {filepath}", dataset_name="codebase_architecture")
                            files_added += 1
                        except Exception:
                            pass
                    await cognee.cognify()
                console.print(f"  [dim]└[/dim] [bold green]{files_added} files indexed into Cognee Graph Memory.[/bold green]")
                continue

            # /memory
            if cmd_lower == "/memory":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                if use_prompt_toolkit:
                    query = await asyncio.get_event_loop().run_in_executor(None, lambda: session.prompt("What memory to recall? "))
                else:
                    from rich.prompt import Prompt as RPrompt
                    query = RPrompt.ask("What memory to recall?")
                query = query.strip()
                with console.status("  [dim]└[/dim] [bold magenta]Querying Cognee Knowledge Graph..."):
                    import cognee
                    results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
                if results:
                    table = Table(title="Cognee Knowledge Graph -- Long-Term Memories", border_style="magenta")
                    table.add_column("Memory / Insight", style="cyan")
                    for res in results:
                        table.add_row(str(res))
                    console.print(table)
                else:
                    console.print("  [dim]└[/dim] [italic red]No memories found in the Cognee graph.[/italic red]")
                continue

            # /history
            if cmd_lower == "/history":
                console.print(f"\n[bold cyan]{cmd}[/bold cyan]")
                if not agent.messages or len(agent.messages) <= 1:
                    console.print("  [dim]└[/dim] [italic red]No session history yet.[/italic red]")
                else:
                    table = Table(title="Omni-Dev Conversation Session History", border_style="cyan")
                    table.add_column("Role", style="bold magenta", width=12)
                    table.add_column("Content / Action", style="cyan")
                    for msg in agent.messages:
                        role = msg.get("role", "unknown").upper()
                        if role == "SYSTEM":
                            content = msg.get("content", "")
                            preview = content.splitlines()[0] if content else ""
                            table.add_row("SYSTEM", f"[dim]System Instruction: {preview[:60]}...[/dim]")
                        elif role == "TOOL":
                            name = msg.get("name", "tool")
                            content = msg.get("content", "")
                            table.add_row("TOOL RESULT", f"[dim]Tool '{name}' returned {len(content)} chars: {content[:100]}...[/dim]")
                        else:
                            content = msg.get("content", "") or ""
                            if msg.get("tool_calls"):
                                tc_desc = ", ".join(t.get("function", {}).get("name", "") for t in msg["tool_calls"])
                                content += f" [dim](Triggered tools: {tc_desc})[/dim]"
                            table.add_row(role, content.strip())
                    console.print(table)
                continue

            # Skip empty input
            if not user_input.strip():
                continue

            # Tool call progress callback (clean indented aesthetic)
            def tool_callback(func_name: str, args: dict):
                markers = {
                    "read_file":       ("[READ]   ", "dim",         f"Reading: {args.get('path', '')}"),
                    "write_file":      ("[CREATE] ", "bold green",  f"Creating: {args.get('path', '')}"),
                    "edit_file":       ("[EDIT]   ", "bold yellow", f"Editing: {args.get('file_path', '')}"),
                    "run_command":     ("[CMD]    ", "bold red",    f"Running: {args.get('command', '')}"),
                    "remember":        ("[MEMORY] ", "bold magenta", f"Memorizing: {args.get('fact', '')[:60]}"),
                    "recall":          ("[RECALL] ", "bold magenta", f"Recalling: {args.get('query', '')}"),
                    "spawn_subagent":  ("[AGENT]  ", "bold cyan",   "Spawning background sub-agent..."),
                    "search_web":      ("[WEB]    ", "bold yellow", f"Web search: {args.get('query', '')}"),
                    "think":           ("[THINK]  ", "dim cyan",    "Thinking..."),
                    "search_codebase": ("[GREP]   ", "bold yellow", f"Grep: '{args.get('pattern', '')}'"),
                    "glob_files":      ("[GLOB]   ", "dim",         f"Pattern: {args.get('pattern', '')}"),
                    "list_dir":        ("[LS]     ", "dim",         f"List: {args.get('path', '.')}"),
                    "read_notebook":   ("[NB-R]   ", "dim",         f"Notebook: {args.get('path', '')}"),
                    "edit_notebook":   ("[NB-E]   ", "bold yellow", f"Notebook: {args.get('path', '')}"),
                    "architect":       ("[PLAN]   ", "bold blue",   f"Planning: {args.get('task', '')[:60]}"),
                    "browser_action":  ("[BROWSER]", "bold cyan",   f"{args.get('action', '').upper()}: {args.get('url', '') or args.get('selector', '') or args.get('direction', '')}"),
                    "read_url_content":("[FETCH]  ", "bold green",  f"URL: {args.get('url', '')}"),
                    "ask_user":        ("[QUESTION]", "bold yellow", f"Asking user: {args.get('question', '')[:60]}"),
                }
                if func_name in markers:
                    marker, style, msg = markers[func_name]
                    console.print(f"  [dim]├─[/dim] [{style}]{marker}{msg}[/{style}]")
                else:
                    console.print(f"  [dim]├─[/dim] [dim][TOOL] {func_name}[/dim]")

            # AUTO-RAG: Deep Memory Retrieval before every message
            import cognee
            past_context = ""
            try:
                deep_query = f"User Request: {user_input} | Recent Agent Actions"
                retrieved = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=deep_query)
                if retrieved:
                    past_context = "\n\n<deep_graph_context>\n" + "\n".join(str(r) for r in retrieved) + "\n</deep_graph_context>"
            except Exception:
                pass

            augmented_prompt = f"{user_input}{past_context}"

            # Execute Task
            console.print("\n  [dim]└[/dim] [bold green]Omni-Dev is acting...[/bold green]")
            final_response = await agent.execute_task(augmented_prompt, progress_callback=tool_callback)

            # AUTO-JOURNALING: Store to Cognee Memory
            try:
                journal_entry = f"User Request: {user_input}\nOmni-Dev Response: {final_response}"
                await cognee.add(journal_entry, dataset_name="user_memory")
                await cognee.cognify()
            except Exception:
                pass

            # Render Response
            console.print()
            console.print("[bold cyan]✨ Omni-Dev[/bold cyan]")
            
            # Print response with a beautiful vertical accent line on the left
            grid = Table.grid(padding=(0, 1))
            grid.add_column(style="bold cyan")
            grid.add_column()
            grid.add_row("│", Markdown(final_response))
            console.print(grid)
            console.print()

        except KeyboardInterrupt:
            console.print("\n  [dim]└[/dim] [italic]Interrupted. Type exit to quit.[/italic]")
        except Exception as e:
            console.print_exception(show_locals=False)


if __name__ == "__main__":
    asyncio.run(main())
