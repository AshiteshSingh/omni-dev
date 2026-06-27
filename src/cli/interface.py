"""
interface.py - Enhanced CLI Interface

This is the main CLI for Omni-Dev. Enhanced with new commands ported from
the TypeScript scratch_repo.

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
from rich.prompt import Prompt
from rich.table import Table

warnings.filterwarnings("ignore", category=UserWarning)
import logging
logging.getLogger("cognee").setLevel(logging.CRITICAL)

from src.agent.core import OmniDevAgent

load_dotenv()

# Rich Console: force_terminal bypasses Windows legacy renderer that ignores stdout reconfigure
console = Console(
    highlight=False,
    force_terminal=True,
    legacy_windows=False,
)


def print_header(agent: OmniDevAgent):
    """Prints the status header."""
    cwd = os.getcwd()
    try:
        branch = subprocess.check_output(
            "git branch --show-current",
            shell=True, text=True, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        ).strip() or "No Git"
    except Exception:
        branch = "No Git"

    model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro").strip()
    if model and "/" not in model:
        lower_m = model.lower()
        if any(k in lower_m for k in ["llama", "mixtral", "gemma", "deepseek", "whisper"]):
            model = "groq/" + model

    tokens = agent.get_token_usage()

    from src.cost_tracker import get_tracker
    cost = get_tracker().total_cost_usd

    # Clean, sleek header bar without directory path clutter
    header_text = (
        f"[bold green]GIT:[/bold green] {branch}   |   "
        f"[bold yellow]MODEL:[/bold yellow] {model}   |   "
        f"[bold magenta]{tokens:,} tok[/bold magenta]   |   "
        f"[bold red]${cost:.4f}[/bold red]"
    )
    console.print()
    console.print(Panel(header_text, title="[bold]Omni-Dev[/bold]", border_style="cyan", padding=(0, 1)))


async def main():
    console.clear()

    # Pure ASCII art banner - no Unicode
    console.print("""[bold cyan]
   ____  __  __ _   _ ___       ____
  / __ \\|  \\/  | \\ | |_ _|     |  _ \\ _____   __
 | |  | | |\\/| |  \\| || |_____ | | | / _ \\ \\ / /
 | |__| | |  | | |\\  || |_____ | |_| \\ __/\\ V /
  \\____/|_|  |_|_| \\_|___|     |____/ \\___|\\_/
[/bold cyan]""")
    console.print("[italic cyan]   Context-Aware Agentic Developer -- Powered by Cognee Graph Memory[/italic cyan]\n")
    console.print("=" * 75 + "\n")

    with console.status("[bold green]Initializing Omni-Dev and loading memories..."):
        try:
            agent = OmniDevAgent()
        except Exception as e:
            console.print(f"[bold red]Failed to initialize agent:[/bold red] {e}")
            return

    console.print("[green]Ready.[/green] Type [bold yellow]exit[/bold yellow] to quit, or [bold yellow]/help[/bold yellow] for commands.\n")
    print_header(agent)

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
            cmd = user_input.strip().lower()

            # Exit
            if cmd in ["exit", "quit"]:
                console.print("[italic]Shutting down Omni-Dev. Goodbye![/italic]")
                break

            # /help
            if cmd == "/help":
                table = Table(title="Omni-Dev Commands", border_style="cyan", show_lines=True)
                table.add_column("Command", style="bold green", no_wrap=True)
                table.add_column("Description", style="white")
                commands = [
                    ("/help", "Show this help menu"),
                    ("/tokens", "View current session token usage and status"),
                    ("/cost", "Detailed session cost breakdown"),
                    ("/model [name]", "Switch LLM provider (e.g., groq/openai/gpt-oss-120b)"),
                    ("/api_key [provider] [key]", "Add an API key securely"),
                    ("/init", "Analyze codebase -> create AGENTS.md project instructions"),
                    ("/doctor", "Diagnose environment: API keys, tools, dependencies"),
                    ("/review [ref]", "AI code review of git diff (e.g., /review HEAD~1)"),
                    ("/ctx_viz", "Visualize conversation context & token counts"),
                    ("/config [key] [val]", "View/set configuration values"),
                    ("/compact", "AI-summarize conversation then clear (keeps Cognee memory)"),
                    ("/memory", "Query Cognee Knowledge Graph directly"),
                    ("/index", "Crawl codebase and push to Cognee Graph Memory"),
                    ("/history", "View agent message history"),
                    ("/commit [msg]", "Create a Git commit"),
                    ("/pwd", "Print current working directory"),
                    ("/ls", "List files in current directory"),
                    ("/clear", "Clear the terminal"),
                    ("exit / quit", "Exit Omni-Dev"),
                ]
                for cmd_name, desc in commands:
                    table.add_row(cmd_name, desc)
                console.print(table)
                continue

            # /clear
            if cmd == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
                continue

            # /pwd
            if cmd == "/pwd":
                console.print(f"[bold cyan]CWD:[/bold cyan] {os.getcwd()}")
                continue

            # /ls
            if cmd == "/ls":
                console.print("[bold cyan]Directory Contents:[/bold cyan]")
                os.system("dir" if os.name == "nt" else "ls -la")
                continue

            # /cost or /tokens
            if cmd in ["/cost", "/tokens", "/status"]:
                print_header(agent)
                from src.cost_tracker import get_tracker
                console.print(Panel(
                    get_tracker().get_summary(),
                    title="[bold magenta]Session Token Usage & Cost[/bold magenta]",
                    border_style="magenta",
                ))
                continue

            # /history
            if cmd == "/history":
                console.print("[bold cyan]Agent Internal Message History:[/bold cyan]")
                for i, msg in enumerate(agent.messages):
                    role = msg.get("role", "?").upper()
                    content = str(msg.get("content", ""))[:200]
                    if len(str(msg.get("content", ""))) > 200:
                        content += "..."
                    tool_calls = msg.get("tool_calls", [])
                    tc_str = f" [{len(tool_calls)} tool calls]" if tool_calls else ""
                    console.print(f"  [dim][{i}][/dim] [bold]{role}[/bold]{tc_str}: {content}")
                continue

            # /commit
            if cmd.startswith("/commit"):
                parts = user_input.strip().split(" ", 1)
                if len(parts) == 2:
                    msg = parts[1].strip()
                    os.system(f'git add -A && git commit -m "{msg}"')
                    console.print("[bold green]Git commit created.[/bold green]")
                else:
                    console.print("[yellow]Usage: /commit <message>[/yellow]")
                continue

            # /compact
            if cmd == "/compact":
                model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
                with console.status("[bold magenta]AI is summarizing conversation before compacting..."):
                    from src.commands.compact import compact_command
                    summary, new_messages = await compact_command(agent.messages, model)
                agent.messages = new_messages
                from src.context import invalidate_context_cache
                invalidate_context_cache()
                agent._context = {}
                console.print("[bold green]Session compacted![/bold green]")
                if summary and not summary.startswith("Error"):
                    console.print(Panel(
                        Markdown(summary[:1500]),
                        title="[bold cyan]Conversation Summary (saved to Cognee)[/bold cyan]",
                        border_style="cyan",
                    ))
                continue

            # /init
            if cmd == "/init":
                with console.status("[bold magenta]Analyzing codebase to create AGENTS.md..."):
                    from src.commands.init_cmd import init_command
                    init_prompt = await init_command()
                console.print("[bold cyan]Running /init -- agent will create AGENTS.md...[/bold cyan]\n")
                with console.status("[bold green]Omni-Dev is analyzing and writing AGENTS.md..."):
                    response = await agent.execute_task(init_prompt)
                console.print(Panel(Markdown(response), title="[bold cyan]/init Result[/bold cyan]", border_style="cyan"))
                continue

            # /doctor
            if cmd == "/doctor":
                with console.status("[bold magenta]Running diagnostics..."):
                    from src.commands.doctor import doctor_command
                    report = await doctor_command()
                console.print(Panel(Markdown(report), title="[bold yellow]Doctor Report[/bold yellow]", border_style="yellow"))
                continue

            # /review
            if cmd.startswith("/review"):
                parts = user_input.strip().split(" ", 1)
                target = parts[1].strip() if len(parts) > 1 else "HEAD"
                with console.status(f"[bold magenta]Getting git diff for: {target}..."):
                    from src.commands.review import review_command
                    review_prompt = await review_command(target)
                if review_prompt.startswith("Error") or review_prompt.startswith("No changes"):
                    console.print(f"[yellow]{review_prompt}[/yellow]")
                    continue
                console.print("[bold cyan]Reviewing code changes...[/bold cyan]\n")
                with console.status("[bold green]AI is reviewing your code..."):
                    response = await agent.execute_task(review_prompt)
                console.print(Panel(Markdown(response), title="[bold cyan]Code Review[/bold cyan]", border_style="cyan"))
                continue

            # /ctx_viz
            if cmd == "/ctx_viz":
                from src.commands.ctx_viz import ctx_viz_command
                report = await ctx_viz_command(agent.messages, agent._context)
                console.print(Panel(Markdown(report), title="[bold blue]Context Visualization[/bold blue]", border_style="blue"))
                continue

            # /config
            if cmd.startswith("/config"):
                parts = user_input.strip().split(" ", 2)
                key = parts[1].strip() if len(parts) > 1 else None
                value = parts[2].strip() if len(parts) > 2 else None
                from src.commands.config_cmd import config_command
                result = await config_command(key, value)
                console.print(Panel(Markdown(result), title="[bold yellow]Config[/bold yellow]", border_style="yellow"))
                continue

            # /bug
            if cmd == "/bug":
                import platform
                model = os.environ.get("OMNI_MODEL", "unknown")
                bug_context = (
                    f"**OS:** {platform.system()} {platform.version()}\n"
                    f"**Python:** {platform.python_version()}\n"
                    f"**Model:** {model}\n"
                    f"**CWD:** {os.getcwd()}\n"
                    f"**Messages:** {len(agent.messages)}\n"
                )
                console.print(Panel(bug_context, title="[bold red]Bug Report Context[/bold red]", border_style="red"))
                continue

            # /model
            if cmd.startswith("/model"):
                parts = user_input.strip().split(" ", 1)
                if len(parts) == 2:
                    new_model = parts[1].strip()
                else:
                    console.print("\n[bold cyan]Select an LLM Provider/Model:[/bold cyan]")
                    console.print("1. OpenAI (gpt-4o)")
                    console.print("2. Anthropic (claude-3-5-sonnet-20241022)")
                    console.print("3. Groq (groq/llama-3.3-70b-versatile)")
                    console.print("4. Google Gemini API (gemini/gemini-1.5-pro)")
                    console.print("5. Google Vertex AI (vertex_ai/gemini-1.5-pro)")
                    console.print("6. Local Ollama (ollama/llama3)")
                    console.print("7. Custom model string")
                    from rich.prompt import IntPrompt
                    choice = IntPrompt.ask("Enter choice", choices=["1","2","3","4","5","6","7"], show_choices=False)
                    model_map = {
                        1: "gpt-4o",
                        2: "claude-3-5-sonnet-20241022",
                        3: "groq/llama-3.3-70b-versatile",
                        4: "gemini/gemini-1.5-pro",
                        5: "vertex_ai/gemini-1.5-pro",
                        6: "ollama/llama3",
                    }
                    if int(choice) in model_map:
                        new_model = model_map[int(choice)]
                    else:
                        new_model = Prompt.ask("[italic]Enter exact litellm model string[/italic]").strip()

                if new_model:
                    if "/" not in new_model:
                        lower_m = new_model.lower()
                        if any(k in lower_m for k in ["llama", "mixtral", "gemma", "deepseek", "whisper"]):
                            new_model = "groq/" + new_model
                        elif "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
                            new_model = "openai/" + new_model
                        elif "claude" in lower_m:
                            new_model = "anthropic/" + new_model
                        elif "gemini" in lower_m:
                            new_model = "gemini/" + new_model

                    os.environ["OMNI_MODEL"] = new_model
                    try:
                        from dotenv import set_key
                        set_key(".env", "OMNI_MODEL", new_model)
                    except Exception:
                        pass
                    console.print(f"[bold green]Model switched to:[/bold green] {new_model}")
                continue

            # /api_key
            if cmd.startswith("/api_key"):
                parts = user_input.strip().split(" ", 2)
                if len(parts) == 3:
                    provider_key = parts[1].strip().upper()
                    if not provider_key.endswith("_API_KEY"):
                        provider_key += "_API_KEY"
                    key_value = parts[2].strip()
                else:
                    console.print("\n[bold cyan]Select API Provider:[/bold cyan]")
                    console.print("1. OpenAI (OPENAI_API_KEY)")
                    console.print("2. Anthropic (ANTHROPIC_API_KEY)")
                    console.print("3. Groq (GROQ_API_KEY)")
                    console.print("4. Google Gemini (GEMINI_API_KEY)")
                    console.print("5. Mistral (MISTRAL_API_KEY)")
                    console.print("6. Together AI (TOGETHERAI_API_KEY)")
                    console.print("7. Custom")
                    from rich.prompt import IntPrompt
                    choice = IntPrompt.ask("Enter choice", choices=["1","2","3","4","5","6","7"], show_choices=False)
                    provider_map = {
                        1: "OPENAI_API_KEY",
                        2: "ANTHROPIC_API_KEY",
                        3: "GROQ_API_KEY",
                        4: "GEMINI_API_KEY",
                        5: "MISTRAL_API_KEY",
                        6: "TOGETHERAI_API_KEY",
                    }
                    if int(choice) in provider_map:
                        provider_key = provider_map[int(choice)]
                    else:
                        provider_key = Prompt.ask("Enter custom env var name").strip().upper()
                        if not provider_key.endswith("_API_KEY"):
                            provider_key += "_API_KEY"
                    key_value = Prompt.ask(f"Enter {provider_key}", password=True).strip()

                if key_value:
                    os.environ[provider_key] = key_value
                    try:
                        from dotenv import set_key
                        set_key(".env", provider_key, key_value)
                    except Exception:
                        pass
                    console.print(f"[bold green]API key saved:[/bold green] {provider_key}")
                continue

            # /index
            if cmd == "/index":
                import glob
                with console.status("[bold magenta]Indexing codebase into Cognee Graph Memory..."):
                    import cognee
                    files_added = 0
                    for filepath in glob.glob(os.path.join(".", "**", "*.*"), recursive=True):
                        if any(x in filepath for x in ["node_modules", ".git", "venv", "__pycache__"]):
                            continue
                        try:
                            await cognee.add(
                                f"Codebase File: {filepath}",
                                dataset_name="codebase_architecture",
                            )
                            files_added += 1
                        except Exception:
                            pass
                    await cognee.cognify()
                console.print(f"[bold green]{files_added} files indexed into Cognee Graph Memory.[/bold green]")
                continue

            # /memory
            if cmd == "/memory":
                query = Prompt.ask("[italic]What memory do you want to recall?[/italic]")
                with console.status("[bold magenta]Querying Cognee Knowledge Graph..."):
                    import cognee
                    results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
                if results:
                    table = Table(title="Cognee Knowledge Graph -- Long-Term Memories", border_style="magenta")
                    table.add_column("Memory / Insight", style="cyan")
                    for res in results:
                        table.add_row(str(res))
                    console.print(table)
                else:
                    console.print("[italic red]No memories found in the Cognee graph.[/italic red]")
                continue

            # Skip empty input
            if not user_input.strip():
                continue

            # Tool call progress callback (plain text markers, no emoji)
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
                }
                if func_name in markers:
                    marker, style, msg = markers[func_name]
                    console.print(f"  [{style}]{marker}{msg}[/{style}]")
                else:
                    console.print(f"  [dim][TOOL]    {func_name}[/dim]")

            # AUTO-RAG: Deep Memory Retrieval before every message
            import cognee
            past_context = ""
            try:
                deep_query = f"User Request: {user_input} | Recent Agent Actions and File Edits"
                retrieved = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=deep_query)
                if retrieved:
                    past_context = (
                        "\n\n<deep_graph_context>\n"
                        + "\n".join(str(r) for r in retrieved)
                        + "\n</deep_graph_context>"
                    )
            except Exception:
                pass

            augmented_prompt = f"{user_input}{past_context}"

            # Execute Task
            console.print("\n")
            with console.status("[bold green]Omni-Dev is thinking and acting..."):
                final_response = await agent.execute_task(augmented_prompt, progress_callback=tool_callback)

            # AUTO-JOURNALING: Store to Cognee Memory
            try:
                journal_entry = f"User Request: {user_input}\nOmni-Dev Response: {final_response}"
                await cognee.add(journal_entry, dataset_name="user_memory")
                await cognee.cognify()
            except Exception:
                pass

            # Render Response
            console.print("\n" + "-" * 75)
            console.print(Panel(
                Markdown(final_response),
                title="[bold cyan]Omni-Dev[/bold cyan]",
                border_style="cyan",
            ))

        except KeyboardInterrupt:
            console.print("\n[italic]Interrupted. Type exit to quit.[/italic]")
        except Exception as e:
            console.print_exception(show_locals=False)


if __name__ == "__main__":
    asyncio.run(main())
