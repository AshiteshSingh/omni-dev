"""
context.py - Python conversion of scratch_repo/src/context.ts

Provides context automatically injected into every system prompt:
- Git status (branch, recent commits, status)
- Directory structure snapshot
- README.md contents
- AGENTS.md / CLAUDE.md instructions
- Code style hints

This mirrors the getContext() function from the scratch_repo TypeScript code.
"""
import os
import subprocess
import asyncio
from functools import lru_cache
from typing import Dict, Optional

# Cache timeout in seconds
_CONTEXT_CACHE: Optional[Dict[str, str]] = None
_CONTEXT_CACHE_CWD: Optional[str] = None


def _run_git(args: list[str]) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.getcwd(),
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return ""


def _is_git_repo() -> bool:
    """Check if the current directory is inside a git repo."""
    result = _run_git(["rev-parse", "--git-dir"])
    return bool(result)


def get_git_status() -> Optional[str]:
    """
    Get comprehensive git status.
    Mirrors getGitStatus() from scratch_repo/src/context.ts.
    """
    if not _is_git_repo():
        return None

    try:
        branch = _run_git(["branch", "--show-current"]) or "unknown"
        main_branch = _run_git(["rev-parse", "--abbrev-ref", "origin/HEAD"]).replace("origin/", "") or "main"
        status = _run_git(["status", "--short"])
        recent_log = _run_git(["log", "--oneline", "-n", "5"])

        # Truncate status if > 200 lines
        status_lines = status.splitlines()
        if len(status_lines) > 200:
            status = "\n".join(status_lines[:200]) + f"\n... (truncated, {len(status_lines) - 200} more lines)"

        return (
            f"Git Status (snapshot at conversation start — not live):\n"
            f"Branch: {branch}\n"
            f"Main branch: {main_branch}\n\n"
            f"Status:\n{status or '(clean)'}\n\n"
            f"Recent commits:\n{recent_log or '(no commits)'}"
        )
    except Exception:
        return None


def get_directory_structure(max_depth: int = 3, max_files: int = 50) -> str:
    """
    Get approximate directory structure.
    Mirrors getDirectoryStructure() from scratch_repo/src/context.ts.
    """
    cwd = os.getcwd()
    lines = []
    count = [0]

    def walk_dir(path: str, prefix: str, depth: int):
        if depth > max_depth or count[0] >= max_files:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        for i, entry in enumerate(entries):
            if count[0] >= max_files:
                lines.append(f"{prefix}...")
                break
            name = entry.name
            # Skip common noise directories
            if name in (".git", "node_modules", "venv", "__pycache__", ".venv", "dist", "build", ".next", "*.pyc"):
                continue
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{name}/")
                count[0] += 1
                if depth < max_depth:
                    walk_dir(entry.path, child_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{name}")
                count[0] += 1

    walk_dir(cwd, "", 0)

    tree_text = "\n".join(lines)
    return (
        f"Below is a snapshot of this project's file structure at the start of the conversation.\n"
        f"This snapshot will NOT update during the conversation.\n\n"
        f"{cwd}\n{tree_text}"
    )


def get_readme() -> Optional[str]:
    """Read README.md from cwd. Mirrors getReadme() from scratch_repo."""
    readme_path = os.path.join(os.getcwd(), "README.md")
    if not os.path.exists(readme_path):
        return None
    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 2000:  # Truncate long READMEs to save tokens
            content = content[:2000] + "\n... (README truncated)"
        return content
    except Exception:
        return None


def get_agents_md() -> Optional[str]:
    """
    Read AGENTS.md or CLAUDE.md for project-specific instructions.
    Mirrors getClaudeFiles() from scratch_repo/src/context.ts.
    """
    for fname in ("AGENTS.md", "CLAUDE.md"):
        fpath = os.path.join(os.getcwd(), fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    return f"Project instructions from {fname}:\n{f.read()}"
            except Exception:
                pass
    return None


def get_nested_instructions(max_depth: int = 4, max_files: int = 20) -> Optional[str]:
    """Discover AGENTS.md / CLAUDE.md files in subdirectories.

    Mirrors getClaudeFiles() in scratch_repo: the agent is told that additional
    instruction files exist deeper in the tree so it reads and follows them when
    working in those directories. Returns ``None`` when none are found beyond the
    project root (the root file is already injected by :func:`get_agents_md`).
    Bounded by depth/count and never raises.
    """
    cwd = os.getcwd()
    skip = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist",
            "build", ".next", ".cognee_data", "scratch_repo"}
    found: list[str] = []
    try:
        base_depth = cwd.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(cwd):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if depth > max_depth:
                dirs[:] = []
                continue
            # Skip the root itself (handled by get_agents_md).
            if os.path.normpath(root) == os.path.normpath(cwd):
                continue
            for fname in ("AGENTS.md", "CLAUDE.md"):
                if fname in files:
                    rel = os.path.relpath(os.path.join(root, fname), cwd)
                    found.append(rel)
            if len(found) >= max_files:
                break
    except Exception:
        return None

    if not found:
        return None
    listing = "\n".join(f"- {p}" for p in found[:max_files])
    return (
        "NOTE: Additional AGENTS.md/CLAUDE.md files were found in subdirectories. "
        "When working in those directories, read and follow the instructions in the "
        f"corresponding file:\n{listing}"
    )


async def get_context() -> Dict[str, str]:
    """
    Get full context dict to inject into system prompt.
    Mirrors getContext() from scratch_repo/src/context.ts.
    
    Returns a dict of context_name -> context_value.
    """
    global _CONTEXT_CACHE, _CONTEXT_CACHE_CWD
    cwd = os.getcwd()

    # Return cached context if we haven't changed directories
    if _CONTEXT_CACHE is not None and _CONTEXT_CACHE_CWD == cwd:
        return _CONTEXT_CACHE

    ctx: Dict[str, str] = {}

    # Run these concurrently
    git_status_task = asyncio.get_event_loop().run_in_executor(None, get_git_status)
    dir_struct_task = asyncio.get_event_loop().run_in_executor(None, get_directory_structure)
    readme_task = asyncio.get_event_loop().run_in_executor(None, get_readme)
    agents_md_task = asyncio.get_event_loop().run_in_executor(None, get_agents_md)
    nested_task = asyncio.get_event_loop().run_in_executor(None, get_nested_instructions)

    git_status, dir_struct, readme, agents_md, nested = await asyncio.gather(
        git_status_task, dir_struct_task, readme_task, agents_md_task, nested_task,
        return_exceptions=True,
    )

    if git_status and not isinstance(git_status, Exception):
        ctx["gitStatus"] = git_status
    if dir_struct and not isinstance(dir_struct, Exception):
        ctx["directoryStructure"] = dir_struct
    if readme and not isinstance(readme, Exception):
        ctx["readme"] = readme
    if agents_md and not isinstance(agents_md, Exception):
        ctx["agentInstructions"] = agents_md
    if nested and not isinstance(nested, Exception):
        ctx["nestedInstructions"] = nested

    _CONTEXT_CACHE = ctx
    _CONTEXT_CACHE_CWD = cwd
    return ctx


def format_system_prompt_with_context(system_prompt: str, context: Dict[str, str]) -> str:
    """
    Append context to system prompt.
    Mirrors formatSystemPromptWithContext() from scratch_repo/src/services/claude.ts.
    """
    if not context:
        return system_prompt

    ctx_parts = [f"\nAs you answer the user's questions, you can use the following context:\n"]
    for key, value in context.items():
        ctx_parts.append(f'<context name="{key}">{value}</context>')

    return system_prompt + "\n" + "\n".join(ctx_parts)


def invalidate_context_cache():
    """Clear the context cache (e.g., after /compact)."""
    global _CONTEXT_CACHE, _CONTEXT_CACHE_CWD
    _CONTEXT_CACHE = None
    _CONTEXT_CACHE_CWD = None
