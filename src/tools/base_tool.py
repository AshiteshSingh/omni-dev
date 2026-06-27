"""
Base Tool class - Python equivalent of the TypeScript Tool interface in scratch_repo.
All tools inherit from this base.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseTool(ABC):
    """
    Abstract base class for all agent tools.
    Mirrors the TypeScript Tool interface from scratch_repo/src/Tool.ts
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The tool's internal name used in function calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown to the LLM."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema parameters definition."""
        ...

    def is_read_only(self) -> bool:
        """If True, tool can be run concurrently with other read-only tools."""
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        """Return True if this tool needs user permission to run."""
        return False

    @abstractmethod
    async def call(self, **kwargs) -> str:
        """Execute the tool and return a result string."""
        ...

    def to_schema(self) -> Dict[str, Any]:
        """Convert this tool to a litellm-compatible JSON schema dict."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required_params,
                },
            },
        }

    @property
    def required_params(self):
        """Override in subclasses to specify required parameter names."""
        return list(self.parameters.keys())
