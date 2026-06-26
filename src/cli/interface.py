import asyncio
import os
import warnings
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.live import Live
from rich.spinner import Spinner

# Suppress annoying library deprecation warnings to keep the CLI clean
warnings.filterwarnings("ignore", category=UserWarning)

from src.agent.core import OmniDevAgent

# Load environment variables
load_dotenv()

console = Console()

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
    console.print("="*60 + "\n")
    
    # Check credentials
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        console.print("[bold red]WARNING:[/bold red] GOOGLE_APPLICATION_CREDENTIALS is not set. Vertex AI might fail.")

    with console.status("[bold green]Initializing Omni-Dev and loading memories...") as status:
        try:
            agent = OmniDevAgent()
        except Exception as e:
            console.print(f"[bold red]Failed to initialize agent:[/bold red] {e}")
            return
            
    console.print("[green]Ready.[/green] Type [bold yellow]exit[/bold yellow] to quit, or [bold yellow]/memory[/bold yellow] to check context.\n")

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]You[/bold cyan]")
            cmd = user_input.strip().lower()
            
            if cmd in ["exit", "quit"]:
                console.print("[italic]Shutting down Omni-Dev...[/italic]")
                break

            if cmd == "/help":
                from rich.table import Table
                table = Table(title="Omni-Dev Commands", border_style="cyan")
                table.add_column("Command", style="bold green")
                table.add_column("Description", style="white")
                table.add_row("/help", "Show this help menu")
                table.add_row("/model <name>", "Switch LLM provider (e.g., gpt-4o, ollama/llama3)")
                table.add_row("/api_key <provider> <key>", "Add an API key securely (e.g., /api_key OPENAI sk-...)")
                table.add_row("/clear", "Clear the terminal screen")
                table.add_row("/index", "Aggressively crawl codebase and push architecture into Graph Memory")
                table.add_row("/compact", "Reset short-term memory to save tokens (keeps long-term graph memory)")
                table.add_row("/memory", "Manually query the Cognee Knowledge Graph")
                console.print(table)
                continue

            if cmd == "/clear":
                import os
                os.system('cls' if os.name == 'nt' else 'clear')
                continue
                
            if cmd.startswith("/model "):
                new_model = cmd.split(" ", 1)[1].strip()
                os.environ["OMNI_MODEL"] = new_model
                from dotenv import set_key
                set_key('.env', 'OMNI_MODEL', new_model)
                console.print(f"[bold green]✅ LLM engine hot-swapped to:[/bold green] {new_model}")
                continue
                
            if cmd.startswith("/api_key "):
                parts = cmd.split(" ", 2)
                if len(parts) == 3:
                    provider_key = parts[1].strip().upper()
                    if not provider_key.endswith("_API_KEY"):
                        provider_key += "_API_KEY"
                    key_value = parts[2].strip()
                    os.environ[provider_key] = key_value
                    from dotenv import set_key
                    set_key('.env', provider_key, key_value)
                    console.print(f"[bold green]✅ API Key saved for:[/bold green] {provider_key}")
                else:
                    console.print("[red]Usage: /api_key <PROVIDER> <key>[/red] (e.g. /api_key OPENAI sk-...)")
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
                    from rich.table import Table
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

            # Callback to show tool usage in the UI
            def tool_callback(func_name, args):
                if func_name == "read_file":
                    console.print(f"[dim]Reading file: {args.get('path')}[/dim]")
                elif func_name == "write_file":
                    console.print(f"[dim]Writing file: {args.get('path')}[/dim]")
                elif func_name == "edit_file":
                    console.print(f"[dim]Editing file (Smart Chunk Replace): {args.get('path')}[/dim]")
                elif func_name == "run_command":
                    console.print(f"[bold yellow]Executing command:[/bold yellow] {args.get('command')}")
                elif func_name == "remember":
                    console.print(f"[magenta]Storing memory:[/magenta] {args.get('fact')}")
                elif func_name == "recall":
                    console.print(f"[magenta]Searching memory for:[/magenta] {args.get('query')}")
                elif func_name == "spawn_subagent":
                    console.print(f"[bold cyan]Spawning Sub-Agent for:[/bold cyan] {args.get('task_description')}")
                elif func_name == "search_web":
                    console.print(f"[yellow]Searching web (SearXNG):[/yellow] {args.get('query')}")
                elif func_name == "think":
                    console.print(f"[bold cyan]Omni-Dev is thinking:[/bold cyan] {args.get('thought')}")
                elif func_name == "search_codebase":
                    console.print(f"[yellow]Searching codebase for:[/yellow] '{args.get('query')}' in {args.get('directory')}")

            # --- AUTO-RAG (Deep Memory Retrieval) ---
            import cognee
            past_context = ""
            try:
                # Pull denser information from the graph
                deep_query = f"User Request: {user_input} | Recent Agent Tool Actions and File Edits"
                retrieved_memories = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=deep_query)
                if retrieved_memories:
                    past_context = "\n\n<deep_graph_context>\n" + "\n".join(str(r) for r in retrieved_memories) + "\n</deep_graph_context>"
            except Exception:
                pass
                
            augmented_prompt = f"{user_input}{past_context}"

            # Execute task with a spinner
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
            console.print("\n[bold cyan]Omni-Dev:[/bold cyan]")
            console.print(Markdown(final_response))
            console.print("---")
            
        except KeyboardInterrupt:
            console.print("\n[italic]Shutting down Omni-Dev...[/italic]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Error during execution:[/bold red] {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
