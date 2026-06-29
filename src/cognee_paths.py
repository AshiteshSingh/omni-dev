"""
cognee_paths.py - Durable, reinstall-proof storage location for Cognee.

By default Cognee writes its system databases (sqlite relational store, the
graph file, and the LanceDB vector store) under the installed package directory
(``site-packages/cognee/.cognee_system``). That location is volatile: it is wiped
on every reinstall/upgrade and is not part of the project, so memory does not
survive across environments.

This module repoints Cognee's DATA root and SYSTEM root into the project's
``.cognee_data`` directory so the knowledge graph is durable and travels with the
repo. It must run BEFORE any cognee operation (add/cognify/search), so it is
imported for its side effect at module-import time from both the CLI interface
and the memory tools.

Cognee v1.2.2 exposes the setters as ``cognee.config.data_root_directory(path)``
and ``cognee.config.system_root_directory(path)`` (NOT ``set_data_root_directory``).
``system_root_directory`` cascades the change to the relational / graph / vector
database paths (``<system_root>/databases``). We discover whatever is actually
available at runtime and also set the corresponding env vars as a belt-and-braces
fallback. Every step is wrapped so a failure can never crash the CLI.
"""
import os

# ── Quiet Cognee's logging BEFORE it is ever imported ──────────────────────
# Cognee uses structlog and binds its own stderr handler at import time, reading
# the LOG_LEVEL env var (default "INFO") then. The stdlib ``logging.setLevel``
# calls elsewhere cannot retroactively silence that handler, so the ONLY reliable
# way to stop the [info]/pipeline log flood in the CLI is to set these before the
# first ``import cognee`` (which happens below in configure_cognee_storage).
# ``setdefault`` so a user can still override with LOG_LEVEL=DEBUG for debugging.
os.environ.setdefault("LOG_LEVEL", "ERROR")          # cognee structlog console level
os.environ.setdefault("COGNEE_CLI_MODE", "true")     # compact, CLI-friendly logging
os.environ.setdefault("COGNEE_LOG_FILE", "true")     # keep full logs on disk, off console
os.environ.setdefault("LITELLM_LOG", "ERROR")
os.environ.setdefault("LITELLM_SET_VERBOSE", "False")
# Cognee runs a blocking LLM connection probe before each pipeline. We have
# already verified the provider works, and the probe both slows startup and can
# false-timeout on a cold Vertex call — skip it everywhere (the CLI set this for
# itself; doing it here covers subagents, tools and scripts too).
os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")

# Project root = two levels up from this file (src/cognee_paths.py -> project/).
PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
COGNEE_DATA_DIR = os.path.join(PROJECT_ROOT, ".cognee_data")
COGNEE_SYSTEM_DIR = os.path.join(COGNEE_DATA_DIR, "system")

_CONFIGURED = False


def configure_cognee_storage() -> str:
    """Point Cognee's DATA + SYSTEM roots into the project ``.cognee_data`` dir.

    Idempotent and fully defensive. Returns the system root directory path that
    was configured (so callers/tests can assert on it).
    """
    global _CONFIGURED

    try:
        os.makedirs(COGNEE_DATA_DIR, exist_ok=True)
        os.makedirs(COGNEE_SYSTEM_DIR, exist_ok=True)
    except Exception:
        pass

    # Belt-and-braces: set the env vars Cognee reads before it builds its config.
    # These are read by cognee's base/relational config when the package is first
    # imported, so setting them is helpful when this module is imported very early.
    for env_key in ("DATA_ROOT_DIRECTORY", "COGNEE_DATA_ROOT_DIRECTORY"):
        try:
            os.environ.setdefault(env_key, COGNEE_DATA_DIR)
        except Exception:
            pass
    for env_key in ("SYSTEM_ROOT_DIRECTORY", "COGNEE_SYSTEM_ROOT_DIRECTORY"):
        try:
            os.environ.setdefault(env_key, COGNEE_SYSTEM_DIR)
        except Exception:
            pass

    try:
        import cognee
    except Exception:
        return COGNEE_SYSTEM_DIR

    cfg = cognee.config

    # DATA root — discover the correct method name at runtime.
    for meth in ("data_root_directory", "set_data_root_directory"):
        fn = getattr(cfg, meth, None)
        if callable(fn):
            try:
                fn(COGNEE_DATA_DIR)
                break
            except Exception:
                continue

    # SYSTEM root — this cascades to relational/graph/vector DB paths.
    for meth in ("system_root_directory", "set_system_root_directory"):
        fn = getattr(cfg, meth, None)
        if callable(fn):
            try:
                fn(COGNEE_SYSTEM_DIR)
                break
            except Exception:
                continue

    _CONFIGURED = True
    return COGNEE_SYSTEM_DIR


def configure_cognee_llm() -> None:
    """Ensure Cognee's LLM + embedding env is populated before any litellm call.

    ``.env`` is the source of truth (Cognee reads it directly via pydantic), but
    litellm reads ``os.environ`` and only after ``load_dotenv()`` has run. This
    helper loads ``.env`` (best effort) and, for Vertex AI, guarantees the
    project/region litellm needs are present. It never raises.

    AUTO-LINK: Cognee's chat LLM follows the agent's ``OMNI_MODEL`` so you set the
    model in ONE place. Embeddings are intentionally NOT linked — the embedding
    model has a fixed vector size and must stay stable across the indexed store
    (changing it would corrupt the existing vectors), so it stays pinned in .env.
    Set ``COGNEE_LLM_MODEL`` to override the link and pin Cognee to a different
    model than the agent.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    except Exception:
        pass

    # Belt-and-braces aliases litellm accepts for the Vertex project/region, so a
    # value supplied under any common name propagates to the others.
    proj = (
        os.environ.get("VERTEXAI_PROJECT")
        or os.environ.get("VERTEX_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    loc = (
        os.environ.get("VERTEXAI_LOCATION")
        or os.environ.get("VERTEX_LOCATION")
        or os.environ.get("CLOUD_ML_REGION")
    )
    if proj:
        for k in ("VERTEXAI_PROJECT", "VERTEX_PROJECT", "GOOGLE_CLOUD_PROJECT"):
            os.environ.setdefault(k, proj)
    if loc:
        for k in ("VERTEXAI_LOCATION", "VERTEX_LOCATION", "CLOUD_ML_REGION"):
            os.environ.setdefault(k, loc)

    # --- Auto-link Cognee's chat LLM to the agent's model (OMNI_MODEL) ---
    # An explicit COGNEE_LLM_MODEL pin always wins.
    chosen = (os.environ.get("COGNEE_LLM_MODEL") or os.environ.get("OMNI_MODEL") or "").strip()
    if chosen:
        provider, api_key = _cognee_provider_for(chosen)
        os.environ["LLM_PROVIDER"] = provider
        os.environ["LLM_MODEL"] = chosen
        if api_key:
            os.environ["LLM_API_KEY"] = api_key


def _cognee_provider_for(model: str) -> tuple[str, str]:
    """Map a litellm model id to (cognee_provider, api_key) for the LLM config.

    Cognee's ``custom`` provider routes any litellm model string through litellm,
    which is what we use for Vertex (auth is via ADC, so the key is a non-empty
    placeholder Cognee requires). Native providers use their own API key env var.
    """
    low = model.lower()
    if low.startswith("vertex_ai/"):
        return "custom", (os.environ.get("LLM_API_KEY") or "vertex-adc")
    if low.startswith("gemini/"):
        return "gemini", (os.environ.get("GEMINI_API_KEY") or "")
    if low.startswith("openai/"):
        return "openai", (os.environ.get("OPENAI_API_KEY") or "")
    if low.startswith("anthropic/"):
        return "anthropic", (os.environ.get("ANTHROPIC_API_KEY") or "")
    if low.startswith("mistral/"):
        return "mistral", (os.environ.get("MISTRAL_API_KEY") or "")
    if low.startswith(("ollama/", "ollama_chat/")):
        return "ollama", "ollama"
    if low.startswith("groq/"):
        return "custom", (os.environ.get("GROQ_API_KEY") or "")
    # Unknown prefix: route through litellm via the custom provider.
    return "custom", (os.environ.get("LLM_API_KEY") or "litellm")


# IMPORTANT ORDERING: set the LLM/embedding env FIRST, because Cognee caches its
# config (lru_cache) the moment it is imported. configure_cognee_storage() below
# imports cognee, so the LLM env must already be in place before that happens.
try:
    configure_cognee_llm()
except Exception:
    pass

# Configure storage location (imports cognee) AFTER the LLM env is set.
try:
    configure_cognee_storage()
except Exception:
    pass
