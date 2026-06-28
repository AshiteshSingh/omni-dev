"""Model Router - single authoritative model-name normalization and routing.

This module consolidates the model-name normalization logic that was previously
duplicated across ``src/cli/interface.py`` (the ``/model`` handler) and
``src/agent/core.py`` (``execute_task``). Both layers call :func:`normalize_model`
so they always agree on the canonical ``provider/model`` form (Requirement 5.2).

Task 2.1 implements only :class:`RouteDecision` and :func:`normalize_model`.
``route()`` and ``get_completion_fn()`` are completed in task 2.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional

# Provider prefixes the router recognizes. When a model identifier already carries
# one of these prefixes, no provider inference is performed.
KNOWN_PROVIDERS = (
    "groq/",
    "openai/",
    "anthropic/",
    "gemini/",
    "vertex_ai/",
    "openrouter/",
    "ollama/",
    "mistral/",
    "deepseek/",
    "huggingface/",
    "azure/",
    "cohere/",
)

# Markers that identify a Cloud_Ollama_Model (hosted at https://ollama.com).
CLOUD_OLLAMA_MARKERS = ("cloud", "-cloud", ":cloud")

# Endpoints (Requirements 1.3, 1.4).
LOCAL_OLLAMA_BASE = "http://localhost:11434"
CLOUD_OLLAMA_BASE = "https://ollama.com"

# Default bounded request timeout in seconds (Requirement 1.1).
DEFAULT_TIMEOUT = 120.0

# Type alias for the injectable model-call function (completed in task 2.4).
CompletionFn = Callable[..., object]


@dataclass(frozen=True)
class RouteDecision:
    """The canonical routing decision shared by the interface and engine layers.

    Carries everything the agent loop needs to issue a request: the canonical
    ``provider/model`` string, the resolved provider, Ollama local/cloud routing,
    endpoint, key, timeout, and an optional ``error`` set when routing is
    impossible (e.g. a Cloud_Ollama_Model without an API key).
    """

    canonical_model: str
    provider: str
    is_ollama: bool
    is_cloud_ollama: bool
    api_base: str | None
    api_key: str | None
    timeout: float = DEFAULT_TIMEOUT
    error: str | None = None


def _strip_surrounding_quotes(text: str) -> str:
    """Remove a single pair of matching surrounding quotes, repeatedly."""
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "`"):
        text = text[1:-1].strip()
    return text


def normalize_model(raw: str) -> str:
    """Normalize a user-supplied model identifier into canonical ``provider/model``.

    The function is pure and idempotent: ``normalize_model(normalize_model(x))``
    equals ``normalize_model(x)`` for any input.

    Normalization rules (Requirements 5.1, 5.3, 5.4, 5.5, 5.6):

    1. Trim surrounding whitespace and matching quotes; collapse repeated ``/``.
    2. Strip a leading ``model `` / ``models/`` prefix; map ``ollama `` -> ``ollama/``.
    3. If no known provider prefix is present, infer one from the identifier.
    4. For Ollama, preserve size tags (needed to address the local model) and
       cloud markers (``cloud`` / ``-cloud`` / ``:cloud``) used for cloud routing.
    """
    if raw is None:
        return ""

    model_name = raw.strip()
    model_name = _strip_surrounding_quotes(model_name)
    if not model_name:
        return ""

    # Collapse repeated separators (Req 5.4).
    while "//" in model_name:
        model_name = model_name.replace("//", "/")

    # Strip leading 'model ' / 'models/' prefixes; map 'ollama ' -> 'ollama/' (Req 5.4).
    lower = model_name.lower()
    if lower.startswith("model "):
        model_name = model_name[6:].strip()
    elif lower.startswith("models/"):
        model_name = model_name[7:].strip()
    elif lower.startswith("ollama "):
        model_name = "ollama/" + model_name[7:].strip().lstrip("/")

    # Re-collapse in case prefix stripping introduced/exposed redundant separators.
    while "//" in model_name:
        model_name = model_name.replace("//", "/")

    if not model_name:
        return ""

    # Infer a provider prefix only when none of the known prefixes is present (Req 5.3).
    # Ollama identities (size tags + cloud markers) are preserved as-is because we
    # never strip the tag (Req 5.5, 5.6).
    if not any(model_name.lower().startswith(p) for p in KNOWN_PROVIDERS):
        lower_m = model_name.lower()
        if "/" in model_name:
            model_name = "openrouter/" + model_name
        elif "gpt" in lower_m or "o1" in lower_m or "o3" in lower_m:
            model_name = "openai/" + model_name
        elif "claude" in lower_m:
            model_name = "anthropic/" + model_name
        elif "gemini" in lower_m:
            model_name = "gemini/" + model_name
        elif "oss" in lower_m or any(
            k in lower_m for k in ("llama", "mixtral", "gemma", "whisper")
        ):
            model_name = "groq/" + model_name
        elif any(k in lower_m for k in ("glm", "qwen", "deepseek", "phi", "yi")):
            model_name = "openrouter/" + model_name

    return model_name


# Per-provider environment variable holding the API key (Requirement 1.4, 6.1).
_PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
}


def _env_get(env: Mapping[str, str], key: str) -> Optional[str]:
    """Read ``key`` from ``env``, stripping surrounding whitespace and quotes.

    Returns ``None`` for a missing or (after stripping) empty value.
    """
    if env is None:
        return None
    value = env.get(key)
    if value is None:
        return None
    value = _strip_surrounding_quotes(str(value).strip())
    return value or None


def _provider_of(canonical: str) -> str:
    """Extract the provider segment from a canonical ``provider/model`` string.

    The ``ollama_chat`` provider prefix maps back to the ``ollama`` provider so
    key/endpoint resolution treats both Ollama prefixes identically.
    """
    if "/" in canonical:
        prefix = canonical.split("/", 1)[0].lower()
        if prefix == "ollama_chat":
            return "ollama"
        return prefix
    return ""


def _resolve_api_key(
    provider: str, is_cloud_ollama: bool, env: Mapping[str, str]
) -> Optional[str]:
    """Resolve the API key for ``provider`` from ``env`` (Requirements 1.4, 6.1)."""
    if provider == "ollama":
        # Local Ollama needs no key; cloud Ollama uses OLLAMA_API_KEY.
        if is_cloud_ollama:
            return _env_get(env, "OLLAMA_API_KEY")
        return None
    env_key = _PROVIDER_KEY_ENV.get(provider)
    if env_key:
        return _env_get(env, env_key)
    return None


def route(raw: str, env: Mapping[str, str]) -> RouteDecision:
    """Build a full :class:`RouteDecision` (local vs cloud Ollama, keys, timeout).

    This function is pure and offline: it performs no network calls and never
    invokes the completion function. Connectivity probing for local Ollama lives
    in :func:`ensure_local_ollama`, which the agent loop may call separately.

    Behavior (Requirements 1.1-1.6, 2.3, 5.2, 5.7):

    * Normalizes ``raw`` via :func:`normalize_model`; on failure falls back to the
      raw string so routing never crashes (Req 5.7).
    * Detects Ollama (``ollama/`` or ``ollama_chat/`` prefix) and cloud Ollama
      (cloud marker in the name or an ``OLLAMA_API_BASE`` containing ``ollama.com``).
    * For tool-capable Ollama models, switches the canonical prefix to
      ``ollama_chat/`` (Req 2.3).
    * Selects the local (``http://localhost:11434`` or ``OLLAMA_API_BASE``) or
      cloud (``https://ollama.com``) endpoint (Req 1.3, 1.4); non-Ollama -> ``None``.
    * Resolves the per-provider API key from ``env``.
    * Sets ``error`` (and sends nothing) when a cloud Ollama model has no key (Req 1.5).
    """
    if env is None:
        env = {}

    # Normalize, never crash (Req 5.7).
    try:
        canonical = normalize_model(raw)
    except Exception:
        canonical = (raw or "").strip()
    if not canonical:
        canonical = (raw or "").strip()

    lower = canonical.lower()
    is_ollama = lower.startswith("ollama/") or lower.startswith("ollama_chat/")

    ollama_api_base = _env_get(env, "OLLAMA_API_BASE")

    # Cloud Ollama detection (Req 1.4): cloud marker in the name OR cloud api base.
    is_cloud_ollama = False
    if is_ollama:
        if any(marker in lower for marker in CLOUD_OLLAMA_MARKERS):
            is_cloud_ollama = True
        elif ollama_api_base and "ollama.com" in ollama_api_base.lower():
            is_cloud_ollama = True

    provider = _provider_of(canonical)

    # Tool-capable Ollama uses the ``ollama_chat/`` provider prefix (Req 2.3).
    # Decide capability via the tool policy on a provisional decision. Import
    # lazily to avoid a hard circular import between the two modules.
    if is_ollama and lower.startswith("ollama/"):
        provisional = RouteDecision(
            canonical_model=canonical,
            provider=provider,
            is_ollama=is_ollama,
            is_cloud_ollama=is_cloud_ollama,
            api_base=None,
            api_key=None,
        )
        try:
            try:
                from src import tool_policy
            except ImportError:  # pragma: no cover - import path fallback
                import tool_policy  # type: ignore
            tools_enabled = tool_policy.supports_tools(provisional)
        except Exception:
            tools_enabled = True
        if tools_enabled:
            canonical = "ollama_chat/" + canonical[len("ollama/"):]
            lower = canonical.lower()

    # Endpoint selection (Req 1.3, 1.4).
    if is_ollama:
        if is_cloud_ollama:
            api_base: Optional[str] = CLOUD_OLLAMA_BASE
        else:
            api_base = ollama_api_base or LOCAL_OLLAMA_BASE
    else:
        api_base = None

    api_key = _resolve_api_key(provider, is_cloud_ollama, env)

    # Cloud Ollama without a key: descriptive error, send nothing (Req 1.5).
    error: Optional[str] = None
    if is_cloud_ollama and not _env_get(env, "OLLAMA_API_KEY"):
        error = (
            f"Cloud Ollama model '{canonical}' requires an Ollama API key, but "
            "none is configured. Set the OLLAMA_API_KEY environment variable "
            "(get a key at https://ollama.com) or switch to a local Ollama model."
        )

    return RouteDecision(
        canonical_model=canonical,
        provider=provider,
        is_ollama=is_ollama,
        is_cloud_ollama=is_cloud_ollama,
        api_base=api_base,
        api_key=api_key,
        timeout=DEFAULT_TIMEOUT,
        error=error,
    )


# Module-global injection point for the model-call function. Tests call
# :func:`set_completion_fn` to inject a FakeBackend so no network is used.
_COMPLETION_FN: Optional[CompletionFn] = None


def set_completion_fn(fn: Optional[CompletionFn]) -> None:
    """Inject (or clear, with ``None``) the completion function used by the agent.

    This is the single injection point that lets the test suite run entirely
    offline against a fake backend (Requirement 8.8).
    """
    global _COMPLETION_FN
    _COMPLETION_FN = fn


def get_completion_fn() -> CompletionFn:
    """Return the callable used to talk to the model.

    Returns the injected function if one was set via :func:`set_completion_fn`;
    otherwise imports ``litellm`` lazily and returns ``litellm.completion``.
    Importing ``litellm`` lazily keeps ``import src.model_router`` cheap and
    avoids requiring the dependency for pure routing/normalization tests.
    """
    if _COMPLETION_FN is not None:
        return _COMPLETION_FN
    import litellm  # lazy import; heavy and only needed for real requests

    return litellm.completion


def _probe_ollama(api_base: str, timeout: float = 2.0) -> bool:
    """Return True if a local Ollama server answers at ``api_base``.

    Import-safe and non-fatal: any error (offline, refused, timeout) -> False.
    """
    import urllib.request

    url = api_base.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= getattr(resp, "status", 200) < 500
    except Exception:
        return False


def ensure_local_ollama(api_base: Optional[str] = None) -> Optional[str]:
    """Probe a local Ollama server and try to start it once if unreachable.

    Intended for the agent loop (not :func:`route`, which must stay offline).
    Returns ``None`` when the server is reachable, otherwise a descriptive
    connectivity error string (Requirement 1.6). Never raises.
    """
    base = api_base or LOCAL_OLLAMA_BASE

    if _probe_ollama(base):
        return None

    # Attempt to start the local server once.
    try:
        import subprocess

        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # ollama binary not installed / cannot launch; fall through to error.
        pass
    else:
        # Give the server a brief moment to come up, then re-probe.
        import time

        for _ in range(10):
            time.sleep(0.5)
            if _probe_ollama(base):
                return None

    return (
        f"Cannot reach a local Ollama server at {base}. Start it by running "
        "'ollama serve' (and 'ollama pull <model>' for the model), or switch to "
        "a cloud provider/model."
    )
