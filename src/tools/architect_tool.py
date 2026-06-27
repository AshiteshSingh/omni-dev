"""
ArchitectTool - Python conversion of scratch_repo/src/tools/ArchitectTool

The ArchitectTool enables high-level planning and design of solutions.
It uses a separate LLM call to generate an implementation plan before execution.
"""
import os
from typing import Any, Dict

import litellm
from .base_tool import BaseTool


ARCHITECT_SYSTEM_PROMPT = """You are a senior software architect. 
When given a task, produce a detailed implementation plan that includes:
1. Files to create or modify
2. Key functions/classes to implement
3. Data flow and architecture decisions
4. Potential edge cases and risks
5. Step-by-step implementation order

Be specific and concrete. Use the actual file paths and function names you'll use.
Do NOT write actual code — just the plan."""


class ArchitectTool(BaseTool):
    """
    Generate a high-level implementation plan before coding.
    Python port of scratch_repo ArchitectTool.
    """

    @property
    def name(self) -> str:
        return "architect"

    @property
    def description(self) -> str:
        return (
            "Generate a high-level implementation plan for a complex task. "
            "Use this BEFORE writing code when tackling large features or refactoring. "
            "The plan will guide your implementation steps. "
            "This uses a separate LLM call optimized for planning."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "task": {
                "type": "string",
                "description": "A detailed description of what you want to build or change.",
            },
            "context": {
                "type": "string",
                "description": "Optional. Relevant context about the codebase, existing architecture, or constraints.",
            },
        }

    @property
    def required_params(self):
        return ["task"]

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, task: str, context: str = "") -> str:
        """Generate an implementation plan."""
        model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")

        user_content = f"Task: {task}"
        if context:
            user_content += f"\n\nContext:\n{context}"

        try:
            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=2048,
            )
            plan = response.choices[0].message.content or "(No plan generated)"

            # Store plan to Cognee memory
            try:
                import cognee
                await cognee.add(
                    f"Architecture Plan for: {task[:100]}\n\n{plan}",
                    dataset_name="architecture_plans",
                )
                await cognee.cognify()
            except Exception:
                pass

            return f"## Implementation Plan\n\n{plan}"

        except Exception as e:
            return f"Error generating plan: {e}"
