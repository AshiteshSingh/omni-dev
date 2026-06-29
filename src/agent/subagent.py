"""
subagent.py - Background sub-agent runner

Runs a headless instance of OmniDevAgent to perform a specific task.
Results are stored to Cognee memory so the main agent can retrieve them.

This file is spawned as a separate process by AgentSubTool / spawn_subagent.
PRESERVED: Cognee memory integration for cross-agent communication.
"""
import sys
import os
import asyncio
import warnings
from dotenv import load_dotenv

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Add project root to path so imports work correctly
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agent.core import OmniDevAgent

# Pin Cognee's durable storage roots into the project .cognee_data store before
# ANY cognee operation. Side-effecting import (self-configures on import), so the
# headless sub-agent process writes to the same graph as the main CLI.
try:
    from src import cognee_paths  # noqa: F401
except Exception:
    pass

# Load environment variables
load_dotenv()


async def run_subagent(task_description: str, subagent_id: str):
    """
    Runs a headless OmniDevAgent to solve a specific task.
    Forces it to record its output to Cognee memory.
    """
    try:
        agent = OmniDevAgent()

        # Instruction ensures the sub-agent saves its results to Cognee
        full_prompt = (
            f"SUBAGENT INSTRUCTION: You are sub-agent '{subagent_id}'. "
            f"Your task is: '{task_description}'.\n\n"
            "You MUST operate fully autonomously without user interaction. "
            "Once you have finished your task, you MUST use the 'remember' tool "
            "to write a detailed final report of what you did and your findings "
            f"into Cognee memory. Tag the report with subagent ID: {subagent_id}. "
            "The main agent will use 'recall' to read your report."
        )

        # Execute the task headlessly (no progress callback)
        await agent.execute_task(full_prompt)

    except Exception as e:
        # If sub-agent fails, log the failure to Cognee memory
        try:
            from src import cognee_paths
            cognee_paths.configure_cognee_storage()
        except Exception:
            pass
        try:
            import cognee
            await cognee.add(
                f"Sub-agent '{subagent_id}' failed with error: {str(e)}",
                dataset_name="subagent_errors",
            )
            await cognee.cognify()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python subagent.py <task_description> <subagent_id>")
        sys.exit(1)

    task_desc = sys.argv[1]
    s_id = sys.argv[2]

    asyncio.run(run_subagent(task_desc, s_id))
