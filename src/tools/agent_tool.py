"""
AgentSubTool - Python conversion of scratch_repo/src/tools/AgentTool/AgentTool.tsx

Spawns a background sub-agent to work on a task independently.
Results are stored to Cognee memory so the main agent can retrieve them.
"""
import os
import sys
import subprocess
import uuid
from typing import Any, Dict

from .base_tool import BaseTool


class AgentSubTool(BaseTool):
    """
    Spawn a detached background sub-agent.
    Python port of scratch_repo AgentTool / existing spawn_subagent tool.
    """

    @property
    def name(self) -> str:
        return "spawn_subagent"

    @property
    def description(self) -> str:
        return (
            "Spawn a detached background sub-agent to work on a task independently. "
            "The sub-agent runs silently in the background and uses 'remember' to save "
            "its final report to Cognee memory. Use 'recall' later to retrieve the results. "
            "Best for long-running tasks that don't need immediate results."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "task_description": {
                "type": "string",
                "description": "A detailed description of what the sub-agent needs to accomplish. Be specific.",
            },
        }

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, task_description: str) -> str:
        """Spawn a background sub-agent process."""
        subagent_id = str(uuid.uuid4())[:8]
        try:
            agent_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "agent")
            )
            subagent_script = os.path.join(agent_dir, "subagent.py")

            if not os.path.exists(subagent_script):
                return f"Error: subagent.py not found at {subagent_script}"

            if os.name == "nt":  # Windows
                CREATE_NO_WINDOW = 0x08000000
                subprocess.Popen(
                    [sys.executable, subagent_script, task_description, subagent_id],
                    creationflags=CREATE_NO_WINDOW,
                    cwd=os.getcwd(),
                )
            else:  # Unix
                subprocess.Popen(
                    [sys.executable, subagent_script, task_description, subagent_id],
                    start_new_session=True,
                    cwd=os.getcwd(),
                )

            return (
                f"✅ Sub-agent '{subagent_id}' spawned in the background.\n"
                f"It will save its findings to Cognee memory when finished.\n"
                f"Use 'recall' with query '{subagent_id}' or a relevant topic to retrieve results."
            )
        except Exception as e:
            return f"Error spawning sub-agent: {e}"
