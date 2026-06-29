"""
Tools package - Python conversion of scratch_repo TypeScript tools.
All tool functions are available for use by the agent core.
"""
from .bash_tool import BashTool
from .file_read_tool import FileReadTool
from .file_edit_tool import FileEditTool
from .file_write_tool import FileWriteTool
from .glob_tool import GlobTool
from .grep_tool import GrepTool
from .ls_tool import LSTool
from .think_tool import ThinkTool
from .notebook_tool import NotebookReadTool, NotebookEditTool
from .architect_tool import ArchitectTool
from .agent_tool import AgentSubTool
from .memory_tools import (
    MemoryReadTool,
    MemoryWriteTool,
    ForgetMemoryTool,
    ImproveMemoryTool,
)
from .web_search_tool import WebSearchTool
from .browser_tool import BrowserTool
from .url_read_tool import UrlReadTool
from .ask_user_tool import AskUserTool
from .query_graph_tool import QueryGraphTool

__all__ = [
    "BashTool",
    "FileReadTool",
    "FileEditTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "LSTool",
    "ThinkTool",
    "NotebookReadTool",
    "NotebookEditTool",
    "ArchitectTool",
    "AgentSubTool",
    "MemoryReadTool",
    "MemoryWriteTool",
    "ForgetMemoryTool",
    "ImproveMemoryTool",
    "WebSearchTool",
    "BrowserTool",
    "UrlReadTool",
    "AskUserTool",
    "QueryGraphTool",
]

def get_all_tools():
    """Return all tool instances (mirrors scratch_repo getAllTools())."""
    return [
        BashTool(),
        FileReadTool(),
        FileEditTool(),
        FileWriteTool(),
        GlobTool(),
        GrepTool(),
        LSTool(),
        ThinkTool(),
        NotebookReadTool(),
        NotebookEditTool(),
        ArchitectTool(),
        AgentSubTool(),
        MemoryReadTool(),
        MemoryWriteTool(),
        ForgetMemoryTool(),
        ImproveMemoryTool(),
        WebSearchTool(),
        BrowserTool(),
        UrlReadTool(),
        AskUserTool(),
        QueryGraphTool(),
    ]

def get_json_schemas():
    """Return all tools as JSON schema definitions for litellm."""
    return [tool.to_schema() for tool in get_all_tools()]
