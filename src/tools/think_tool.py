"""
ThinkTool - Python conversion of scratch_repo/src/tools/ThinkTool/ThinkTool.tsx

Allows the agent to think out loud and reason through complex problems.
The thought is logged to Cognee memory for later retrieval.
"""
from typing import Any, Dict

from .base_tool import BaseTool


class ThinkTool(BaseTool):
    """
    Let the agent reason through a complex problem before acting.
    Python port of scratch_repo ThinkTool.
    
    Unlike the original, thoughts are also stored to Cognee for future recall.
    """

    @property
    def name(self) -> str:
        return "think"

    @property
    def description(self) -> str:
        return (
            "Use this tool to think out loud, reason through complex bugs, or architect a plan "
            "before taking action. Your reasoning will be logged to memory for future reference. "
            "Use this when you need to analyze a problem methodically before writing code or running commands."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "thought": {
                "type": "string",
                "description": "Your detailed reasoning, analysis, or architectural plan.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, thought: str) -> str:
        """Log the thought to memory and return confirmation."""
        # We store to Cognee memory - this preserves the memory integration.
        # Pin durable storage roots FIRST so writes land in the project
        # .cognee_data store (never site-packages).
        try:
            from src import cognee_paths
            cognee_paths.configure_cognee_storage()
        except Exception:
            pass
        try:
            import cognee
            await cognee.add(f"Agent Thought Process: {thought}", dataset_name="agent_thoughts")
            await cognee.cognify()
        except Exception:
            pass  # Thought tool should not fail even if memory fails

        return "Thought logged to memory successfully."
