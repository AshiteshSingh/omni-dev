import asyncio
import os
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.live import Live
from rich.spinner import Spinner
from agent import OmniDevAgent

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
            
            if user_input.strip().lower() in ["exit", "quit"]:
                console.print("[italic]Shutting down Omni-Dev...[/italic]")
                break
                
            if user_input.strip().lower() == "/memory":
                query = Prompt.ask("[italic]What memory do you want to recall?[/italic]")
                with console.status("[bold magenta]Querying Cognee Knowledge Graph...") as status:
                    import cognee
                    results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
                    if results:
                        for res in results:
                            console.print(f"- {res}")
                    else:
                        console.print("[italic]Nothing found.[/italic]")
                continue

            if not user_input.strip():
                continue

            # Callback to show tool usage in the UI
            def tool_callback(func_name, args):
                if func_name == "read_file":
                    console.print(f"[dim]Reading file: {args.get('path')}[/dim]")
                elif func_name == "write_file":
                    console.print(f"[dim]Writing file: {args.get('path')}[/dim]")
                elif func_name == "run_command":
                    console.print(f"[bold yellow]Executing command:[/bold yellow] {args.get('command')}")
                elif func_name == "remember":
                    console.print(f"[magenta]Storing memory:[/magenta] {args.get('fact')}")
                elif func_name == "recall":
                    console.print(f"[magenta]Searching memory for:[/magenta] {args.get('query')}")

            # Execute task with a spinner
            with console.status("[bold green]Omni-Dev is thinking and acting...") as status:
                final_response = await agent.execute_task(user_input, progress_callback=tool_callback)

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
