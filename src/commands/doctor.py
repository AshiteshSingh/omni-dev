"""
doctor.py - Python conversion of scratch_repo/src/commands/doctor.ts

Diagnoses the environment: checks API keys, dependencies, git status, etc.
Provides actionable feedback on what is missing or misconfigured.
"""
import os
import subprocess
import sys
from typing import List, Tuple


def check_env_var(name: str) -> Tuple[bool, str]:
    """Check if an environment variable is set."""
    val = os.environ.get(name, "")
    if val:
        masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
        return True, masked
    return False, "(not set)"


def check_command(cmd: str) -> Tuple[bool, str]:
    """Check if a command is available."""
    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = result.stdout.strip().split("\n")[0]
        return True, version
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "not found"


def check_python_package(package: str) -> Tuple[bool, str]:
    """Check if a Python package is installed."""
    try:
        import importlib
        mod = importlib.import_module(package)
        version = getattr(mod, "__version__", "installed")
        return True, version
    except ImportError:
        return False, "not installed"


async def doctor_command() -> str:
    """
    Run environment diagnostics.
    Mirrors doctor.ts from scratch_repo.
    """
    lines = ["## 🩺 Omni-Dev Doctor Report\n"]

    # --- API Keys ---
    lines.append("### 🔑 API Keys")
    api_keys = [
        ("ANTHROPIC_API_KEY", "Anthropic Claude"),
        ("OPENAI_API_KEY", "OpenAI GPT"),
        ("GROQ_API_KEY", "Groq"),
        ("GEMINI_API_KEY", "Google Gemini"),
        ("GOOGLE_APPLICATION_CREDENTIALS", "Google Cloud / Vertex AI"),
        ("AWS_ACCESS_KEY_ID", "AWS Bedrock"),
        ("SEARXNG_URL", "SearXNG Web Search"),
    ]
    for env_name, display in api_keys:
        ok, val = check_env_var(env_name)
        status = "✅" if ok else "⚠️ "
        lines.append(f"  {status} {display} ({env_name}): {val}")

    # --- Current Model ---
    model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro (default)")
    lines.append(f"\n### 🤖 Active Model\n  🧠 {model}")

    # --- System Tools ---
    lines.append("\n### 🛠️ System Tools")
    tools = [
        ("git", "Git"),
        ("python", "Python"),
        ("node", "Node.js"),
        ("npm", "npm"),
        ("rg", "ripgrep (fast search)"),
    ]
    for cmd, display in tools:
        ok, version = check_command(cmd)
        status = "✅" if ok else "⚠️ "
        lines.append(f"  {status} {display}: {version}")

    # --- Python Packages ---
    lines.append("\n### 📦 Python Dependencies")
    packages = [
        ("litellm", "LiteLLM"),
        ("cognee", "Cognee Memory"),
        ("rich", "Rich (UI)"),
        ("dotenv", "python-dotenv"),
        ("requests", "requests"),
    ]
    for pkg, display in packages:
        ok, version = check_python_package(pkg)
        status = "✅" if ok else "❌"
        lines.append(f"  {status} {display} ({pkg}): {version}")

    # --- Working Directory ---
    cwd = os.getcwd()
    lines.append(f"\n### 📁 Working Directory\n  {cwd}")

    # Check if git repo
    try:
        result = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            branch_result = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=3)
            branch = branch_result.stdout.strip() or "detached HEAD"
            lines.append(f"  ✅ Git repo detected (branch: {branch})")
        else:
            lines.append("  ⚠️  Not a git repository")
    except Exception:
        lines.append("  ⚠️  Git not available")

    # Check for AGENTS.md / CLAUDE.md
    for fname in ("AGENTS.md", "CLAUDE.md"):
        if os.path.exists(os.path.join(cwd, fname)):
            lines.append(f"  ✅ {fname} found (project instructions loaded)")
            break
    else:
        lines.append("  💡 No AGENTS.md found. Run /init to create one.")

    lines.append("\n---\n✅ Doctor complete. Fix any ❌/⚠️ items above for best performance.")
    return "\n".join(lines)
