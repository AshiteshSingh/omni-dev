"""
WebSearchTool - Python port of the existing web search capability.

Uses SearXNG for web searching.
"""
import os
from typing import Any, Dict, Optional

import requests
from .base_tool import BaseTool


class WebSearchTool(BaseTool):
    """
    Search the internet using SearXNG.
    """

    @property
    def name(self) -> str:
        return "search_web"

    @property
    def description(self) -> str:
        return (
            "Search the internet using SearXNG for up-to-date information, documentation, or solutions. "
            "Returns top 5 results with title, URL, and snippet. "
            "Use this when you need current information not in your training data."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, query: str) -> str:
        """Search the web and return formatted results."""
        try:
            searxng_url = os.environ.get("SEARXNG_URL", "https://searx.be")
            response = requests.get(
                f"{searxng_url}/search",
                params={"q": query, "format": "json"},
                timeout=15,
                headers={"User-Agent": "OmniDev/1.0"},
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                if not results:
                    return "No search results found."
                formatted = []
                for r in results[:5]:
                    formatted.append(
                        f"**{r.get('title', 'No title')}**\n"
                        f"URL: {r.get('url', '')}\n"
                        f"{r.get('content', '')}"
                    )
                return "\n\n---\n\n".join(formatted)
            return f"Search failed with HTTP {response.status_code}"
        except requests.Timeout:
            return "Search timed out. Try again or use a different query."
        except Exception as e:
            return f"Error searching the web: {e}"
