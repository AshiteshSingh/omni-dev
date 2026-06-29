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
        ("MISTRAL_API_KEY", "Mistral AI"),
        ("OPENROUTER_API_KEY", "OpenRouter"),
        ("OLLAMA_API_KEY", "Ollama Cloud API"),
        ("OLLAMA_API_BASE", "Ollama API Base URL"),
        ("SEARXNG_URL", "SearXNG Web Search"),
    ]
    for env_name, display in api_keys:
        ok, val = check_env_var(env_name)
        status = "✅" if ok else "⚠️ "
        lines.append(f"  {status} {display} ({env_name}): {val}")

    # --- Current Model ---
    model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro (default)")
    lines.append(f"\n### 🤖 Active Model\n  🧠 {model}")
    
    # Check model capabilities
    model_lower = model.lower()
    is_cloud_ollama = model_lower.startswith("ollama/") and any(
        k in model_lower for k in ["cloud", "-cloud", ":cloud"]
    )
    is_local_ollama = model_lower.startswith("ollama/") and not is_cloud_ollama
    no_tool_support = not is_cloud_ollama and (
        is_local_ollama or any(k in model_lower for k in [
            "gemma", "mistral", "neural-chat", "orca", "dolphin"
        ])
    )
    if no_tool_support:
        lines.append("  ⚠️  This model has limited tool support (file/command execution limited)")
    else:
        lines.append("  ✅ This model supports full tool use")
    
    # Check Ollama if applicable
    if "ollama/" in model_lower:
        lines.append("\n### 🦙 Ollama Configuration")
        api_base = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")
        api_key = os.environ.get("OLLAMA_API_KEY", "")
        lines.append(f"  API Base: {api_base}")
        if "ollama.com" in api_base.lower():
            lines.append(f"  Mode: Cloud")
            if api_key:
                lines.append(f"  API Key: {'✅ Set' if api_key else '❌ Not set'}")
            else:
                lines.append(f"  API Key: ❌ Not set (required for cloud)")
        else:
            lines.append(f"  Mode: Local")
            # Try to connect to local Ollama
            try:
                import requests
                resp = requests.get("http://localhost:11434/api/tags", timeout=2)
                if resp.status_code == 200:
                    lines.append(f"  ✅ Local Ollama server is running")
                else:
                    lines.append(f"  ❌ Local Ollama server not responding")
            except Exception:
                lines.append(f"  ❌ Local Ollama server offline. Run: ollama serve")

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

    # --- Cognee Memory Health ---
    lines.append("\n### 🧠 Cognee Memory Health")
    try:
        import cognee as cg  # noqa: F401
        # Read the ACTUAL configured store location (pinned to project .cognee_data
        # by src/cognee_paths.py), not the volatile site-packages default.
        try:
            from src import cognee_paths
            cognee_paths.configure_cognee_storage()
            db_path = os.path.join(cognee_paths.COGNEE_SYSTEM_DIR, "databases")
        except Exception:
            import os as _os
            db_path = _os.path.join(_os.path.dirname(cg.__file__), ".cognee_system", "databases")

        if os.path.exists(db_path):
            db_file = os.path.join(db_path, "cognee_db")
            if os.path.exists(db_file):
                size_kb = os.path.getsize(db_file) // 1024
                lines.append(f"  ✅ Cognee DB found: {db_file}")
                lines.append(f"  📦 DB size: {size_kb} KB {'(has stored memories)' if size_kb > 10 else '(empty — no memories yet)'}")
            else:
                lines.append("  ⚠️  Cognee DB directory exists but no database file yet")
                lines.append("  💡 Talk to Omni-Dev and memories will be stored automatically")
        else:
            lines.append("  ⚠️  Cognee DB directory not found — memories not yet initialized")
        lines.append(f"  📁 DB location: {db_path}")

        # LLM/embedding wiring — the graph layer is dead without these.
        try:
            from cognee.infrastructure.llm.config import get_llm_config
            from cognee.infrastructure.databases.vector.embeddings.config import get_embedding_config
            lc = get_llm_config()
            ec = get_embedding_config()
            llm_ok = bool(getattr(lc, "llm_api_key", None))
            emb_ok = bool(getattr(ec, "embedding_api_key", None)) or bool(getattr(ec, "embedding_endpoint", None))
            lines.append(f"  {'✅' if llm_ok else '❌'} LLM provider: {lc.llm_provider}/{lc.llm_model} (api key {'set' if llm_ok else 'MISSING'})")
            lines.append(f"  {'✅' if emb_ok else '❌'} Embeddings: {ec.embedding_provider}/{ec.embedding_model} (api key {'set' if emb_ok else 'MISSING'})")
            if not (llm_ok and emb_ok):
                lines.append("  💡 Graph memory needs LLM + embedding credentials. Set LLM_PROVIDER/LLM_API_KEY")
                lines.append("     and EMBEDDING_PROVIDER/EMBEDDING_API_KEY in .env, then run: python verify_memory.py")
        except Exception:
            pass

        lines.append("  ℹ️  API version: Cognee 1.2.2 — full lifecycle: remember / recall / improve / forget (+ add/cognify/search)")
    except Exception as e:
        lines.append(f"  ❌ Cognee memory check failed: {e}")

    lines.append("\n---\n✅ Doctor complete. Fix any ❌/⚠️ items above for best performance.")
    return "\n".join(lines)
