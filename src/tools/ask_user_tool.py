"""
ask_user_tool.py - Interactive Question Tool for Omni-Dev

Allows the agent to pause execution and ask the user clarifying questions mid-task.
"""
import asyncio
from typing import Any, Dict
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from src.tools.base_tool import BaseTool

console = Console(highlight=False, force_terminal=True, legacy_windows=False)


class AskUserTool(BaseTool):
    """Tool for pausing execution to ask the user a clarifying question."""

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a clarifying question mid-task and wait for their response. "
            "Use this when requirements are underspecified, when resolving design ambiguity, or confirming preferences. "
            "Do NOT stop or end your turn if you need input; call this tool to pause, get the user's answer, and continue autonomously."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "question": {
                "type": "string",
                "description": "The clear question or decision needed from the user.",
            },
        }

    @property
    def required_params(self):
        return ["question"]

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, question: str, **kwargs) -> str:
        """Pause execution, prompt user in terminal, and return their answer."""
        if not question:
            return "Error: Question parameter is required."

        def _ask():
            console.print("\n")
            console.print(Panel(f"[bold white]{question}[/bold white]", title="[bold yellow]❓ Agent Clarification Needed[/bold yellow]", border_style="yellow"))
            return Prompt.ask(" [bold yellow]Your Answer[/bold yellow]").strip()

        # Pause the REPL's live spinner while we read input so it doesn't fight
        # the prompt for the terminal, then restore it.
        try:
            from src.cli import ui_state
        except Exception:
            ui_state = None

        paused = ui_state.pause_status() if ui_state else None
        try:
            # Run the blocking prompt in an executor thread to keep the async
            # loop responsive while waiting for the user's answer.
            user_reply = await asyncio.get_event_loop().run_in_executor(None, _ask)
        finally:
            if ui_state:
                ui_state.resume_status(paused)
        return f"User replied: '{user_reply}'"
