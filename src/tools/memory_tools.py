"""
MemoryTools - Python port of scratch_repo MemoryReadTool and MemoryWriteTool.

IMPORTANT: These tools use Cognee as the memory backend.
The Cognee integration (cognee.add, cognee.cognify, cognee.search) is
PRESERVED exactly as in the original agent/core.py.
"""
from typing import Any, Dict

import cognee
from .base_tool import BaseTool


class MemoryWriteTool(BaseTool):
    """
    Store a fact or context into long-term Cognee graph memory.
    Python port of scratch_repo MemoryWriteTool.
    Cognee memory integration is preserved exactly.
    """

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact, user preference, or project context into long-term Cognee graph memory. "
            "Use this to persist important information across sessions. "
            "The information can be retrieved later using 'recall'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "fact": {
                "type": "string",
                "description": "The fact, preference, or context to remember permanently.",
            },
        }

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, fact: str) -> str:
        """Store the fact in Cognee memory."""
        try:
            # PRESERVED: Original Cognee memory write pattern
            await cognee.add(fact, dataset_name="user_memory")
            await cognee.cognify()
            return "✅ Fact successfully saved to long-term Cognee graph memory."
        except Exception as e:
            return f"Error saving to memory: {e}"


class MemoryReadTool(BaseTool):
    """
    Search long-term Cognee graph memory for past context.
    Python port of scratch_repo MemoryReadTool.
    Cognee memory integration is preserved exactly.
    """

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term Cognee graph memory for past context, facts, or user preferences. "
            "Use this to retrieve information stored in previous sessions or by sub-agents. "
            "Returns insights from the knowledge graph."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "What you want to search for in long-term memory.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, query: str) -> str:
        """Search Cognee memory for relevant information."""
        try:
            # PRESERVED: Original Cognee memory search pattern
            results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
            if results:
                return "\n".join(str(res) for res in results)
            return "No relevant memories found in the Cognee knowledge graph."
        except Exception as e:
            return f"Error recalling from memory: {e}"
