"""
init_cmd.py - Python conversion of scratch_repo/src/commands/init.ts

The /init command analyzes the codebase and creates an AGENTS.md file
with build commands, code style guidelines, and project context.
Mirrors the TypeScript init command which created CLAUDE.md.
"""
import os
from typing import List, Dict


INIT_PROMPT = """Please analyze this codebase and create an AGENTS.md file containing:

1. **Build/Test/Lint Commands** — especially for running a single test, building the project, and linting
2. **Code Style Guidelines** — including:
   - Import order and conventions
   - Naming conventions (variables, functions, classes, files)
   - Type annotation requirements
   - Error handling patterns
   - Comment/docstring style
3. **Project Architecture** — brief overview of directory structure and key modules
4. **Agent Instructions** — specific instructions for AI coding assistants working in this repo

The AGENTS.md file will be read by AI coding agents (like yourself) that operate in this repository.
Keep it concise (30-50 lines). Use markdown formatting.

If an AGENTS.md or CLAUDE.md already exists, improve it by adding missing sections.
If there's a .cursorrules or copilot-instructions.md, incorporate those rules too.

Start by reading the directory structure and key config files (package.json, pyproject.toml, setup.py, etc.)
before writing the file."""


async def init_command() -> str:
    """
    Initialize AGENTS.md for the current project.
    Returns the prompt to send to the agent.
    Mirrors init.ts from scratch_repo.
    """
    cwd = os.getcwd()
    existing_files = []
    for fname in ("AGENTS.md", "CLAUDE.md", ".cursorrules", "pyproject.toml", "package.json", "setup.py", "Makefile"):
        if os.path.exists(os.path.join(cwd, fname)):
            existing_files.append(fname)

    context = ""
    if existing_files:
        context = f"\nExisting relevant files found: {', '.join(existing_files)}"

    return INIT_PROMPT + context
