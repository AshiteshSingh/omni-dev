import asyncio
import os
import subprocess
import warnings
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

# Suppress annoying library deprecation warnings and verbose cognee logs
warnings.filterwarnings("ignore", category=UserWarning)
import logging
logging.getLogger("cognee").setLevel(logging.CRITICAL)

from src.agent.core import OmniDevAgent

# Load environment variables
load_dotenv()

console = Console()

def print_header(agent):
    """Prints a premium status header similar to Claude Code."""
    cwd = os.getcwd()
    try:
        branch = subprocess.check_output("git branch --show-current", shell=True, text=True, stderr=subprocess.STDOUT).strip()
        if not branch:
            branch = "No Git Repo"
    except:
        branch = "No Git Repo"
        
    model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
    tokens = agent.get_token_usage()
    
    header_text = f"📁 [bold cyan]{cwd}[/bold cyan] │ 🌿 [bold green]{branch}[/bold green] │ 🧠 [bold yellow]{model}[/bold yellow] │ 🪙 [bold magenta]{tokens} tokens[/bold magenta]"
    console.print()
    console.print(Panel(header_text, title="Omni-Dev Context", border_style="cyan", padding=(0, 2)))

async def main():
    console.clear()
    
    # Professional ASCII Art Banner
    ascii_banner = """[bold cyan]
   ____  __  __ _   _ ___       ____  FV 
  / __ \|  \/  | \ | |_ _|     |  _ \ _____   __
 | |  | | |\/| |  \| || |_____ | | | / _ \ \ / /
 | |__| | |  | | |\  || |_____ | |_| \ __/\ V / 
  \____/|_|  |_|_| \_|___|     |____/ \___|\_/  
[/bold cyan]"""
    console.print(ascii_banner)
    console.print("[italic cyan]   Your Context-Aware, Agentic Developer Assistant[/italic cyan]\n")
    console.print("="*75 + "\n")
    
    with console.status("[bold green]Initializing Omni-Dev and loading memories...") as status:
        try:
            agent = OmniDevAgent()
        except Exception as e:
            console.print(f"[bold red]Failed to initialize agent:[/bold red] {e}")
            return
            
    console.print("[green]Ready.[/green] Type [bold yellow]exit[/bold yellow] to quit, or [bold yellow]/help[/bold yellow] for commands.\n")

    while True:
        try:
            print_header(agent)
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
            cmd = user_input.strip().lower()
            
            if cmd in ["exit", "quit"]:
                console.print("[italic]Shutting down Omni-Dev...[/italic]")
                break

            if cmd == "/help":
                table = Table(title="Omni-Dev Commands", border_style="cyan")
                table.add_column("Command", style="bold green")
                table.add_column("Description", style="white")
                table.add_row("/help", "Show this help menu")
                table.add_row("/model <name>", "Switch LLM provider (e.g., gpt-4o, ollama/llama3)")
                table.add_row("/api_key <provider> <key>", "Add an API key securely (e.g., /api_key OPENAI sk-...)")
                table.add_row("/pwd", "Print the current working directory")
                table.add_row("/ls", "List files in the current directory")
                table.add_row("/cost", "Show total session tokens used")
                table.add_row("/history", "View internal AI message history")
                table.add_row("/commit <msg>", "Create a Git commit instantly")
                table.add_row("/clear", "Clear the terminal screen")
                table.add_row("/index", "Aggressively crawl codebase and push architecture into Graph Memory")
                table.add_row("/compact", "Reset short-term memory to save tokens (keeps long-term graph memory)")
                table.add_row("/memory", "Manually query the Cognee Knowledge Graph")
                console.print(table)
                continue

            if cmd == "/clear":
                os.system('cls' if os.name == 'nt' else 'clear')
                continue
                
            if cmd == "/pwd":
                console.print(f"📁 [bold cyan]Current Directory:[/bold cyan] {os.getcwd()}")
                continue
                
            if cmd == "/ls":
                console.print("[bold cyan]Directory Contents:[/bold cyan]")
                os.system('dir' if os.name == 'nt' else 'ls -la')
                continue
                
            if cmd == "/cost":
                console.print(f"🪙 [bold magenta]Session Tokens Used:[/bold magenta] {agent.get_token_usage()}")
                continue
                
            if cmd == "/history":
                console.print("[bold cyan]Agent Internal Message History:[/bold cyan]")
                for msg in agent.messages:
                    role = msg.get("role", "unknown")
                    # truncate long content
                    content = str(msg.get("content", ""))[:200] + ("..." if len(str(msg.get("content", ""))) > 200 else "")
                    console.print(f"[dim][{role.upper()}][/dim] {content}")
                continue
                
            if cmd.startswith("/commit "):
                msg = user_input.split(" ", 1)[1].strip()
                os.system(f'git commit -m "{msg}"')
                console.print("[bold green]✅ Commit executed.[/bold green]")
                continue

            if cmd.startswith("/model"):
                parts = user_input.strip().split(" ", 1)
                if len(parts) == 2:
                    new_model = parts[1].strip()
                else:
                    # Interactive Mode
                    console.print("\n[bold cyan]Select an LLM Provider/Model:[/bold cyan]")
                    console.print("1. OpenAI (gpt-4o)")
                    console.print("2. Anthropic (claude-3-5-sonnet-20240620)")
                    console.print("3. Groq (groq/llama-3.3-70b-versatile)")
                    console.print("4. Google (gemini/gemini-1.5-pro)")
                    console.print("5. Local Ollama (ollama/llama3)")
                    console.print("6. Custom Model String")
                    from rich.prompt import IntPrompt
                    choice = IntPrompt.ask("Enter choice", choices=["1", "2", "3", "4", "5", "6"], show_choices=False)
                    
                    model_map = {
                        1: "gpt-4o", 
                        2: "claude-3-5-sonnet-20240620", 
                        3: "groq/llama-3.3-70b-versatile", 
                        4: "gemini/gemini-1.5-pro",
                        5: "ollama/llama3"
                    }
                    if int(choice) in model_map:
                        new_model = model_map[int(choice)]
                    else:
                        new_model = Prompt.ask("[italic]Enter exact litellm model string (e.g. groq/qwen-2.5-32b)[/italic]").strip()

                if new_model:
                    os.environ["OMNI_MODEL"] = new_model
                    from dotenv import set_key
                    set_key('.env', 'OMNI_MODEL', new_model)
                    console.print(f"[bold green]✅ LLM engine hot-swapped to:[/bold green] {new_model}")
                continue
                
            if cmd.startswith("/api_key"):
                # If they passed it inline, e.g., /api_key GROQ sk-...
                parts = user_input.strip().split(" ", 2)
                if len(parts) == 3:
                    provider_key = parts[1].strip().upper()
                    if not provider_key.endswith("_API_KEY"):
                        provider_key += "_API_KEY"
                    key_value = parts[2].strip()
                else:
                    # Interactive Mode
                    console.print("\n[bold cyan]Select an API Provider:[/bold cyan]")
                    console.print("1. OpenAI (OPENAI_API_KEY)")
                    console.print("2. Anthropic (ANTHROPIC_API_KEY)")
                    console.print("3. Groq (GROQ_API_KEY)")
                    console.print("4. Google Gemini / Vertex (GEMINI_API_KEY)")
                    console.print("5. Custom Provider")
                    from rich.prompt import IntPrompt
                    choice = IntPrompt.ask("Enter choice", choices=["1", "2", "3", "4", "5"], show_choices=False)
                    
                    provider_map = {1: "OPENAI_API_KEY", 2: "ANTHROPIC_API_KEY", 3: "GROQ_API_KEY", 4: "GEMINI_API_KEY"}
                    if int(choice) in provider_map:
                        provider_key = provider_map[int(choice)]
                    else:
                        provider_key = Prompt.ask("[italic]Enter Custom Provider Prefix (e.g. OLLAMA)[/italic]").strip().upper()
                        if not provider_key.endswith("_API_KEY"):
                            provider_key += "_API_KEY"
                            
                    key_value = Prompt.ask(f"[italic]Enter your {provider_key}[/italic]", password=True).strip()

                if key_value:
                    os.environ[provider_key] = key_value
                    from dotenv import set_key
                    set_key('.env', provider_key, key_value)
                    console.print(f"[bold green]✅ API Key securely saved for:[/bold green] {provider_key}")
                continue
                
            if cmd == "/index":
                with console.status("[bold magenta]Aggressively Indexing Codebase to Graph Database...") as status:
                    import cognee
                    import glob
                    files_added = 0
                    for filepath in glob.glob(os.path.join('.', '**', '*.*'), recursive=True):
                        if 'node_modules' in filepath or '.git' in filepath or 'venv' in filepath or '__pycache__' in filepath:
                            continue
                        try:
                            await cognee.add(f"Project Architecture Node: {filepath}", dataset_name="codebase_architecture")
                            files_added += 1
                        except:
                            pass
                    await cognee.cognify()
                    console.print(f"[bold green]✅ Success: {files_added} files mathematically mapped into Cognee Graph.[/bold green]")
                continue
                
            if cmd == "/compact":
                agent.compact_session()
                console.print("[bold green]✅ Short-term session memory compacted! Token count reset.[/bold green]")
                continue

            if cmd == "/memory":
                query = Prompt.ask("[italic]What memory do you want to recall from the Knowledge Graph?[/italic]")
                with console.status("[bold magenta]Querying Cognee Graph Database for Long-Term Memory...") as status:
                    import cognee
                    results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
                    
                    if results:
                        console.print("\n[bold green]✅ AI Amnesia Solved. Memories retrieved:[/bold green]")
                        table = Table(title="🧠 Cognee Knowledge Graph Insights", border_style="magenta")
                        table.add_column("Memory / Insight", style="cyan")
                        for res in results:
                            table.add_row(str(res))
                        console.print(table)
                    else:
                        console.print("[italic red]No relevant long-term memories found in the graph.[/italic red]")
                continue

            if not user_input.strip():
                continue

            # Premium UI Callback for Tools
            def tool_callback(func_name, args):
                if func_name == "read_file":
                    console.print(f"📖 [dim]Reading file:[/dim] [cyan]{args.get('path')}[/cyan]")
                elif func_name == "write_file":
                    console.print(f"📝 [bold green]Creating file:[/bold green] {args.get('path')}")
                elif func_name == "edit_file":
                    console.print(f"✂️ [bold yellow]Surgically Editing:[/bold yellow] {args.get('path')}")
                elif func_name == "run_command":
                    console.print(f"⚡ [bold red]Running Terminal Command:[/bold red] {args.get('command')}")
                elif func_name == "remember":
                    console.print(f"🧠 [bold magenta]Committing to Graph Memory:[/bold magenta] {args.get('fact')}")
                elif func_name == "recall":
                    console.print(f"🔍 [bold magenta]Searching Graph Memory for:[/bold magenta] {args.get('query')}")
                elif func_name == "spawn_subagent":
                    console.print(f"🤖 [bold cyan]Spawning Sub-Agent:[/bold cyan] Working on background task...")
                elif func_name == "search_web":
                    console.print(f"🌐 [bold yellow]Searching the Web:[/bold yellow] {args.get('query')}")
                elif func_name == "think":
                    console.print(f"🤔 [dim cyan]Omni-Dev is thinking out loud...[/dim cyan]")
                elif func_name == "search_codebase":
                    console.print(f"🕵️ [bold yellow]Searching Codebase for:[/bold yellow] '{args.get('query')}'")

            # --- AUTO-RAG (Deep Memory Retrieval) ---
            import cognee
            past_context = ""
            try:
                deep_query = f"User Request: {user_input} | Recent Agent Tool Actions and File Edits"
                retrieved_memories = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=deep_query)
                if retrieved_memories:
                    past_context = "\n\n<deep_graph_context>\n" + "\n".join(str(r) for r in retrieved_memories) + "\n</deep_graph_context>"
            except Exception:
                pass
                
            augmented_prompt = f"{user_input}{past_context}"

            # Execute task
            console.print("\n")
            with console.status("[bold green]Omni-Dev is thinking and acting...") as status:
                final_response = await agent.execute_task(augmented_prompt, progress_callback=tool_callback)
                
                # --- AUTO-JOURNALING (Memory Storage) ---
                try:
                    journal_entry = f"User Request: {user_input}\nOmni-Dev Response: {final_response}"
                    await cognee.add(journal_entry, dataset_name="user_memory")
                    await cognee.cognify()
                except Exception:
                    pass

            # Print final response beautifully
            console.print("\n" + "─"*75)
            console.print(Panel(Markdown(final_response), title="[bold cyan]Omni-Dev[/bold cyan]", border_style="cyan"))
            
        except KeyboardInterrupt:
            console.print("\n[italic]Shutting down Omni-Dev...[/italic]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Error during execution:[/bold red] {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
