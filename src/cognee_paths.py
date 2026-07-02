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

# Project root = the directory where the user invoked the CLI (their repo).
PROJECT_ROOT = os.path.abspath(os.getcwd())
COGNEE_DATA_DIR = os.path.join(PROJECT_ROOT, ".cognee_data")
COGNEE_SYSTEM_DIR = os.path.join(COGNEE_DATA_DIR, "system")

_CONFIGURED = False


def prime_storage_env() -> str:
    """Set storage env vars + create dirs WITHOUT importing cognee.

    The cheap, import-free part of :func:`configure_cognee_storage`. Startup uses
    this to pin the durable storage roots (cognee picks them up whenever it is
    first imported later) without paying the multi-second ``import cognee`` cost
    up front. Returns the system root path. Never raises.
    """
    try:
        os.makedirs(COGNEE_DATA_DIR, exist_ok=True)
        os.makedirs(COGNEE_SYSTEM_DIR, exist_ok=True)
    except Exception:
        pass

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
    return COGNEE_SYSTEM_DIR


def configure_cognee_storage() -> str:
    """Point Cognee's DATA + SYSTEM roots into the project ``.cognee_data`` dir.

    Idempotent and fully defensive. Returns the system root directory path that
    was configured (so callers/tests can assert on it). Imports cognee (heavy) —
    call lazily; startup should use :func:`prime_storage_env` instead.
    """
    global _CONFIGURED

    # Cheap part first (env vars + dirs), no import.
    prime_storage_env()

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


# ── Embedding model selection (cloud vs local) ─────────────────────────────
# Presets the /embedding command offers. Each: (provider, model, dimensions,
# endpoint). The embedding model is independent of the chat model (OMNI_MODEL)
# because its vector dimension must stay stable across the indexed store.
EMBEDDING_PRESETS = {
    # Cloud: Google Vertex text-embedding-004 (768 dims). Uses your Vertex creds.
    "cloud":  ("custom", "vertex_ai/text-embedding-004", 768, ""),
    # Local: fastembed runs a small sentence-transformers model on CPU, fully
    # offline, no server (requires the 'fastembed' package).
    "local":  ("fastembed", "BAAI/bge-small-en-v1.5", 384, ""),
    # Local via Ollama (needs 'ollama serve' + 'ollama pull nomic-embed-text').
    "ollama": ("ollama", "nomic-embed-text", 768, "http://localhost:11434"),
}


def get_embedding_info() -> tuple[str, str, str]:
    """Return the currently-configured (provider, model, dimensions)."""
    return (
        os.environ.get("EMBEDDING_PROVIDER", "") or "",
        os.environ.get("EMBEDDING_MODEL", "") or "",
        os.environ.get("EMBEDDING_DIMENSIONS", "") or "",
    )


def set_embedding(provider: str, model: str, dimensions, endpoint: str = "",
                  persist: bool = True) -> None:
    """Set the embedding provider/model/dimensions in env (and persist to .env)."""
    os.environ["EMBEDDING_PROVIDER"] = provider
    os.environ["EMBEDDING_MODEL"] = model
    os.environ["EMBEDDING_DIMENSIONS"] = str(dimensions)
    if endpoint:
        os.environ["EMBEDDING_ENDPOINT"] = endpoint
    if persist:
        try:
            from dotenv import set_key
            envp = os.path.join(PROJECT_ROOT, ".env")
            set_key(envp, "EMBEDDING_PROVIDER", provider)
            set_key(envp, "EMBEDDING_MODEL", model)
            set_key(envp, "EMBEDDING_DIMENSIONS", str(dimensions))
            if endpoint:
                set_key(envp, "EMBEDDING_ENDPOINT", endpoint)
        except Exception:
            pass


def backup_databases(label: str = "") -> str:
    """Move the Cognee vector/graph DB aside so a fresh store is built with the
    new embedding dimensions. Returns the backup path (or "" if nothing moved).

    The SimpleMemory JSON store is a separate file and is NOT affected, so facts
    saved via `remember` survive an embedding switch.
    """
    import shutil
    import time as _t
    db = os.path.join(COGNEE_SYSTEM_DIR, "databases")
    if not os.path.isdir(db):
        return ""
    tag = label or "switch"
    dest = os.path.join(COGNEE_SYSTEM_DIR, f"databases_backup_{tag}_{_t.strftime('%Y%m%d_%H%M%S')}")
    try:
        shutil.move(db, dest)
        return dest
    except Exception:
        return ""


def cloud_embedding_for_cli() -> tuple[str, str, int, str]:
    """Pick the cloud embedding model that matches the CLI's provider (OMNI_MODEL),
    so 'same as the CLI' uses the same cloud account/credentials.
    Returns (provider, model, dimensions, endpoint).
    """
    model = (os.environ.get("OMNI_MODEL") or "").lower()
    if model.startswith("gemini/"):
        return ("gemini", "text-embedding-004", 768, "")
    if model.startswith("openai/"):
        return ("openai", "text-embedding-3-small", 1536, "")
    if model.startswith("vertex_ai/"):
        return ("custom", "vertex_ai/text-embedding-004", 768, "")
    if model.startswith("mistral/"):
        return ("mistral", "mistral-embed", 1024, "")
    # Fallback: the default cloud (Vertex) preset.
    return EMBEDDING_PRESETS["cloud"]


# IMPORTANT ORDERING: set the LLM/embedding env FIRST, because Cognee caches its
# config (lru_cache) the moment it is imported. configure_cognee_storage() below
# imports cognee, so the LLM env must already be in place before that happens.
try:
    configure_cognee_llm()
except Exception:
    pass

# Configure storage location AFTER the LLM env is set. Use the cheap, import-free
# primer at module load so importing this module does NOT drag in the heavy
# ``cognee`` package; the full config (which imports cognee) runs lazily on the
# first memory operation via configure_cognee_storage().
try:
    prime_storage_env()
except Exception:
    pass
