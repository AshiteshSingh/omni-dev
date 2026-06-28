"""Tool Capability Policy.

Decides, for a given model, whether native tool/function-calling schemas should
be sent to the model. This replaces the old ``disable_tools_for_model`` heuristic
in ``src/agent/core.py`` which blanket-disabled tools for *all* local Ollama
models, even tool-capable ones (the root cause of the "not agentic" behavior).

The policy is expressed as two tables plus a decision function:

* ``NO_TOOL_MODELS`` - substrings of models known to LACK function calling.
* ``TOOL_CAPABLE``   - modern tool-capable model families (allow list).
* ``supports_tools(route)`` - the per-model decision.

Matching is case-insensitive substring / family matching against the route's
canonical model identifier (falling back to ``provider`` + model).

Requirements: 2.1, 2.2, 2.3
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids hard import at runtime
    from src.model_router import RouteDecision


# Substrings of models known to LACK native function-calling support. A match
# here disables tool schemas for the model regardless of how it is served.
NO_TOOL_MODELS: set[str] = {
    "gemma:2b",
    "gemma:7b",
    "gemma2:2b",
    "gemma2:9b",
    "gemma:latest",
    "orca-mini",
    "orca2",
    "phi",          # phi / phi:2 / phi-2 (older small models lacking tool calling)
    "phi:2",
    "phi-2",
    "tinyllama",
    "tinydolphin",
    "dolphin",
    "neural-chat",
    "starling-lm",
    "openchat",
    "vicuna",
    "wizardlm",
    "stablelm",
    "falcon",
    "mpt",
    "redpajama",
    "llama2",
    "codellama",
    "deepseek-coder",
    "stable-code",
}


# Modern tool-capable model families. A match here forces tools ON (allow list),
# overriding the optimistic-by-default heuristic for local models.
TOOL_CAPABLE: set[str] = {
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "llama-3.1",
    "llama-3.2",
    "llama-3.3",
    "qwen2.5",
    "qwen2",
    "qwen3",
    "mistral-nemo",
    "mistral-large",
    "mistral-small",
    "mixtral",
    "command-r",
    "command-r-plus",
    "firefunction",
    "firefunction-v2",
    "hermes3",
    "nous-hermes2",
    "granite3",
    "llama4",
}


def _model_text(route: Any) -> str:
    """Return a lowercased identifier string for matching.

    Prefers ``route.canonical_model``; falls back to ``provider`` + model-ish
    attributes so the function works even with a partially-formed route object.
    """
    parts: list[str] = []
    canonical = getattr(route, "canonical_model", None)
    if canonical:
        parts.append(str(canonical))
    provider = getattr(route, "provider", None)
    if provider:
        parts.append(str(provider))
    # Defensive: some callers may pass a plain string identifier.
    if not parts and isinstance(route, str):
        parts.append(route)
    return " ".join(parts).lower()


def _matches_any(text: str, families: set[str]) -> bool:
    """Case-insensitive substring/family match of any entry against ``text``."""
    return any(family.lower() in text for family in families)


def _is_ollama(route: Any, text: str) -> bool:
    flag = getattr(route, "is_ollama", None)
    if flag is not None:
        return bool(flag)
    return "ollama" in text


def _is_cloud_ollama(route: Any, text: str) -> bool:
    flag = getattr(route, "is_cloud_ollama", None)
    if flag is not None:
        return bool(flag)
    return _is_ollama(route, text) and any(
        marker in text for marker in ("cloud", "-cloud", ":cloud", "ollama.com")
    )


def supports_tools(route: "RouteDecision") -> bool:
    """Decide whether native tool/function-calling schemas should be sent.

    Decision rules (Requirements 2.1, 2.2):

    1. Any model matching ``NO_TOOL_MODELS`` -> ``False`` (deny list always wins),
       regardless of provider or local/cloud serving.
    2. Cloud providers (groq/openai/anthropic/gemini/vertex_ai/openrouter/
       mistral/cohere and cloud Ollama) -> ``True`` (deny list already handled).
    3. Local Ollama -> ``True`` if the model matches the ``TOOL_CAPABLE`` allow
       list / a known tool-capable family.
    4. Unknown local Ollama models -> optimistic ``True``; the agent loop has a
       fallback-without-tools retry if the model rejects tool schemas.
    """
    text = _model_text(route)

    # 1. Deny list always wins.
    if _matches_any(text, NO_TOOL_MODELS):
        return False

    # 2. Non-Ollama (cloud) providers are tool-capable unless denied above.
    if not _is_ollama(route, text):
        return True

    # 2b. Cloud Ollama is a hosted API that supports tool schemas.
    if _is_cloud_ollama(route, text):
        return True

    # 3. Local Ollama in the allow list / known tool-capable family.
    if _matches_any(text, TOOL_CAPABLE):
        return True

    # 4. Unknown local Ollama model: optimistic True (loop retries without tools).
    return True
