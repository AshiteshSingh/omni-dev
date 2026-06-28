"""Hypothesis strategies for the Omni-Dev property-based test suite.

These generators are imported by the property tests written in later tasks. They
deliberately model the *input space* the design's correctness properties range
over, with smart constraints so generated values are representative rather than
purely random noise. Nothing here asserts behavior; this module only produces data.

Strategy groups (mirroring the design's "Generators" section):

* :func:`model_identifiers` — provider-prefixed, bare, and Ollama identifiers
  (with/without size tags and cloud markers), optionally wrapped in noise
  (surrounding quotes, whitespace, ``//`` separators) for normalization tests.
* :func:`assistant_content` — prose interleaved with fenced code blocks, embedded
  tool-call JSON (valid and invalid), and literal ``\\n`` / ``\\t`` escapes, for
  output-cleaning / rendering tests.
* :func:`tool_call_sequences` — lists of read-only and mutating tool calls carrying
  scripted per-tool latency and scripted results/errors, for loop tests.
* :func:`config_dicts` — arbitrary global/project configs plus partial and corrupt
  variants, for config-store tests.
* :func:`file_content_pairs` — (old, new) text pairs for diff classification tests.
* :func:`call_record_sequences` — sequences of per-call token/cost records for
  cost/token accumulation tests.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

#: Known provider prefixes the router recognizes (without the trailing slash).
PROVIDER_PREFIXES = [
    "groq",
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "ollama",
    "mistral",
    "deepseek",
    "cohere",
]

#: Bare model names that carry no provider prefix (the router must infer one).
BARE_MODEL_NAMES = [
    "gpt-4o",
    "gpt-4o-mini",
    "o1-preview",
    "o3-mini",
    "claude-3-5-sonnet",
    "gemini-1.5-pro",
    "mixtral-8x7b",
    "gpt-oss-120b",
    "glm-4",
    "qwen2.5-coder",
    "deepseek-chat",
    "phi-3",
    "yi-34b",
]

#: Ollama model family roots used to build local/cloud Ollama identifiers.
OLLAMA_FAMILIES = [
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "qwen2.5",
    "mistral-nemo",
    "gemma2",
    "phi",
    "tinyllama",
    "codellama",
    "command-r",
]

#: Size tags that may be appended to a local Ollama model name.
OLLAMA_SIZE_TAGS = ["", ":1b", ":3b", ":7b", ":8b", ":13b", ":70b", ":latest"]

#: Cloud markers identifying a Cloud_Ollama_Model.
CLOUD_MARKERS = ["-cloud", ":cloud", "cloud"]


@st.composite
def _clean_model_identifier(draw: st.DrawFn) -> str:
    """Draw a syntactically clean model identifier (before any noise is added)."""
    kind = draw(st.sampled_from(["prefixed", "bare", "ollama_local", "ollama_cloud"]))
    if kind == "prefixed":
        prefix = draw(st.sampled_from(PROVIDER_PREFIXES))
        name = draw(st.sampled_from(BARE_MODEL_NAMES + OLLAMA_FAMILIES))
        return f"{prefix}/{name}"
    if kind == "bare":
        return draw(st.sampled_from(BARE_MODEL_NAMES))
    if kind == "ollama_local":
        family = draw(st.sampled_from(OLLAMA_FAMILIES))
        tag = draw(st.sampled_from(OLLAMA_SIZE_TAGS))
        prefix = draw(st.sampled_from(["ollama/", ""]))
        return f"{prefix}{family}{tag}"
    # ollama_cloud
    family = draw(st.sampled_from(OLLAMA_FAMILIES))
    marker = draw(st.sampled_from(CLOUD_MARKERS))
    prefix = draw(st.sampled_from(["ollama/", ""]))
    return f"{prefix}{family}{marker}"


@st.composite
def model_identifiers(draw: st.DrawFn, *, with_noise: bool = True) -> str:
    """Strategy for model identifiers, optionally wrapped in normalization noise.

    Noise variants exercise the normalization rules: surrounding single/double
    quotes, leading/trailing whitespace, repeated ``//`` separators, and a leading
    ``model ``/``models/`` prefix.
    """
    identifier = draw(_clean_model_identifier())
    if not with_noise:
        return identifier
    if draw(st.booleans()):
        sep_noise = draw(st.sampled_from(["", "/", "//", "///"]))
        if sep_noise and "/" in identifier:
            identifier = identifier.replace("/", sep_noise, 1)
    if draw(st.booleans()):
        prefix = draw(st.sampled_from(["", "model ", "models/"]))
        identifier = prefix + identifier
    if draw(st.booleans()):
        quote = draw(st.sampled_from(["'", '"', "`"]))
        identifier = f"{quote}{identifier}{quote}"
    if draw(st.booleans()):
        lpad = draw(st.text(alphabet=" \t", max_size=3))
        rpad = draw(st.text(alphabet=" \t", max_size=3))
        identifier = f"{lpad}{identifier}{rpad}"
    return identifier


def ollama_local_identifiers() -> st.SearchStrategy[str]:
    """Local Ollama identifiers (family + optional size tag, no cloud marker)."""
    return st.builds(
        lambda fam, tag, pfx: f"{pfx}{fam}{tag}",
        st.sampled_from(OLLAMA_FAMILIES),
        st.sampled_from(OLLAMA_SIZE_TAGS),
        st.sampled_from(["ollama/", ""]),
    )


def ollama_cloud_identifiers() -> st.SearchStrategy[str]:
    """Cloud Ollama identifiers (family + cloud marker)."""
    return st.builds(
        lambda fam, marker, pfx: f"{pfx}{fam}{marker}",
        st.sampled_from(OLLAMA_FAMILIES),
        st.sampled_from(CLOUD_MARKERS),
        st.sampled_from(["ollama/", ""]),
    )


# ---------------------------------------------------------------------------
# Assistant content (prose + code fences + tool-call JSON + literal escapes)
# ---------------------------------------------------------------------------

_PROSE_WORDS = [
    "Here", "is", "the", "answer", "result", "function", "value", "consider",
    "note", "however", "therefore", "example", "summary", "done", "complete",
]

KNOWN_TOOL_NAMES = [
    "read_file",
    "write_file",
    "file_edit",
    "run_command",
    "grep",
    "glob",
    "ls",
    "search_web",
    "think",
]


def _prose() -> st.SearchStrategy[str]:
    """A short line of human-readable prose."""
    return st.lists(st.sampled_from(_PROSE_WORDS), min_size=1, max_size=12).map(
        lambda words: " ".join(words) + "."
    )


def _fenced_code_block() -> st.SearchStrategy[str]:
    """A fenced code block whose body may itself contain braces and escapes."""
    lang = st.sampled_from(["", "python", "json", "js", "bash", "html"])
    body = st.lists(
        st.sampled_from(
            [
                "x = 1",
                "print('hi')",
                "const a = {b: 1};",
                "<div>\\n</div>",
                "if (x) { return; }",
                "SELECT * FROM t;",
                '{"k": "v"}',
            ]
        ),
        min_size=1,
        max_size=4,
    ).map(lambda lines: "\n".join(lines))
    return st.builds(lambda l, b: f"```{l}\n{b}\n```", lang, body)


def _valid_tool_call_json() -> st.SearchStrategy[str]:
    """A well-formed embedded tool-call JSON object (``name`` + ``arguments``)."""
    return st.builds(
        lambda name, args: json.dumps({"name": name, "arguments": args}),
        st.sampled_from(KNOWN_TOOL_NAMES),
        st.dictionaries(
            keys=st.sampled_from(["path", "command", "pattern", "query", "content"]),
            values=st.text(max_size=20),
            max_size=3,
        ),
    )


def _invalid_tool_call_json() -> st.SearchStrategy[str]:
    """A malformed / partial tool-call JSON snippet (unbalanced braces, etc.)."""
    return st.sampled_from(
        [
            '{"name": "read_file", "arguments": {',
            '{"name": , "arguments": {}}',
            '{name: "run_command"}',
            '{"arguments": {"path": "a"}}',  # missing name
            '```json\n{"name": "grep"\n```',
            "{ broken json here }",
        ]
    )


def _literal_escapes() -> st.SearchStrategy[str]:
    """A fragment containing literal escape sequences such as ``\\n`` / ``\\t``."""
    return st.lists(
        st.sampled_from(["line1\\nline2", "col1\\tcol2", "a\\r\\nb", "tab\\there"]),
        min_size=1,
        max_size=3,
    ).map(lambda parts: " ".join(parts))


@st.composite
def assistant_content(draw: st.DrawFn) -> str:
    """Assistant message content interleaving prose, code, tool-JSON, and escapes.

    The pieces are shuffled together so tests must handle tool-call JSON and
    literal escapes appearing anywhere relative to prose and fenced code blocks.
    """
    fragments: List[str] = []
    n = draw(st.integers(min_value=1, max_value=6))
    for _ in range(n):
        choice = draw(
            st.sampled_from(
                ["prose", "code", "valid_tool", "invalid_tool", "escapes"]
            )
        )
        if choice == "prose":
            fragments.append(draw(_prose()))
        elif choice == "code":
            fragments.append(draw(_fenced_code_block()))
        elif choice == "valid_tool":
            fragments.append(draw(_valid_tool_call_json()))
        elif choice == "invalid_tool":
            fragments.append(draw(_invalid_tool_call_json()))
        else:
            fragments.append(draw(_literal_escapes()))
    return "\n\n".join(fragments)


def clean_assistant_content() -> st.SearchStrategy[str]:
    """Assistant content guaranteed to contain NO tool-call markers.

    Useful for the "legitimate text preserved" half of the no-leakage property,
    where stripping tool calls must not remove any real answer text.
    """
    return st.lists(
        st.one_of(_prose(), _fenced_code_block()), min_size=1, max_size=5
    ).map(lambda parts: "\n\n".join(parts))


# ---------------------------------------------------------------------------
# Tool-call sequences (read-only + mutating, with latency and results/errors)
# ---------------------------------------------------------------------------

#: Tools that do not mutate state (eligible for concurrent execution).
READ_ONLY_TOOLS = ["read_file", "grep", "glob", "ls", "search_web", "think"]

#: Tools that mutate state (must run serially).
MUTATING_TOOLS = ["write_file", "file_edit", "run_command"]


@st.composite
def tool_call_specs(draw: st.DrawFn, *, read_only_only: bool = False) -> Dict[str, Any]:
    """A single scripted tool-call spec.

    Carries the tool ``name``, whether it is ``read_only``, the model-supplied
    ``arguments``, a scripted ``latency`` (seconds, small) used to exercise
    out-of-order completion under concurrency, and a scripted outcome that is
    either a ``result`` string or an ``error`` message (never both).
    """
    if read_only_only:
        name = draw(st.sampled_from(READ_ONLY_TOOLS))
        read_only = True
    else:
        name = draw(st.sampled_from(READ_ONLY_TOOLS + MUTATING_TOOLS))
        read_only = name in READ_ONLY_TOOLS

    arguments = draw(
        st.dictionaries(
            keys=st.sampled_from(["path", "command", "pattern", "query"]),
            values=st.text(max_size=16),
            max_size=3,
        )
    )
    latency = draw(st.floats(min_value=0.0, max_value=0.05))
    raises = draw(st.booleans())
    spec: Dict[str, Any] = {
        "name": name,
        "read_only": read_only,
        "arguments": arguments,
        "latency": latency,
    }
    if raises:
        spec["error"] = draw(st.text(min_size=1, max_size=24))
    else:
        spec["result"] = draw(st.text(max_size=40))
    return spec


def tool_call_sequences(
    *, min_size: int = 1, max_size: int = 6, read_only_only: bool = False
) -> st.SearchStrategy[List[Dict[str, Any]]]:
    """A sequence of scripted tool-call specs (a single round of tool calls)."""
    return st.lists(
        tool_call_specs(read_only_only=read_only_only),
        min_size=min_size,
        max_size=max_size,
    )


def read_only_tool_sequences(
    *, min_size: int = 2, max_size: int = 6
) -> st.SearchStrategy[List[Dict[str, Any]]]:
    """A sequence of exclusively read-only tool-call specs (concurrency tests)."""
    return tool_call_sequences(
        min_size=min_size, max_size=max_size, read_only_only=True
    )


# ---------------------------------------------------------------------------
# Config dicts (arbitrary + partial/corrupt)
# ---------------------------------------------------------------------------

def _json_scalars() -> st.SearchStrategy[Any]:
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=24),
    )


def global_config_dicts() -> st.SearchStrategy[Dict[str, Any]]:
    """Arbitrary global-config dicts using the known global keys (any may be absent)."""
    return st.fixed_dictionaries(
        {},
        optional={
            "activeModel": st.one_of(st.none(), model_identifiers(with_noise=False)),
            "numStartups": st.integers(min_value=0, max_value=10_000),
            "verbose": st.booleans(),
            "theme": st.sampled_from(["omni-dark", "omni-light", "default"]),
            "costThreshold": st.floats(
                min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False
            ),
            "tokenWarningThreshold": st.integers(min_value=0, max_value=10_000_000),
            "costThresholdAcknowledged": st.booleans(),
            "ollamaApiBase": st.one_of(st.none(), st.just("http://localhost:11434")),
            "terminalSetup": st.one_of(st.none(), st.text(max_size=16)),
            "mcpServers": st.dictionaries(
                st.text(min_size=1, max_size=8), st.dictionaries(
                    st.text(max_size=8), _json_scalars(), max_size=3
                ),
                max_size=3,
            ),
        },
    )


def project_config_dicts() -> st.SearchStrategy[Dict[str, Any]]:
    """Arbitrary project-config dicts using the known project keys (any may be absent)."""
    return st.fixed_dictionaries(
        {},
        optional={
            "activeModel": st.one_of(st.none(), model_identifiers(with_noise=False)),
            "allowedTools": st.lists(
                st.sampled_from(
                    [
                        "search_web",
                        "run_command",
                        "run_command(git commit:*)",
                        "run_command(npm install:*)",
                        "read_file",
                    ]
                ),
                max_size=6,
                unique=True,
            ),
            "history": st.lists(st.text(min_size=1, max_size=20), max_size=20),
            "hasTrustDialogAccepted": st.booleans(),
            "mcpServers": st.dictionaries(
                st.text(min_size=1, max_size=8), _json_scalars(), max_size=3
            ),
            "context": st.dictionaries(
                st.text(max_size=8), _json_scalars(), max_size=3
            ),
        },
    )


def corrupt_config_text() -> st.SearchStrategy[str]:
    """Strings that are NOT valid top-level JSON objects (corrupt config files)."""
    return st.one_of(
        st.just(""),
        st.just("{ not json"),
        st.just("[1, 2, 3]"),  # valid JSON but not an object
        st.just("null"),
        st.just('{"unterminated": '),
        st.text(max_size=40).filter(lambda s: not s.strip().startswith("{")),
    )


# ---------------------------------------------------------------------------
# File content pairs (for diffs)
# ---------------------------------------------------------------------------

def _file_lines() -> st.SearchStrategy[List[str]]:
    return st.lists(
        st.sampled_from(
            [
                "import os",
                "def main():",
                "    return 0",
                "x = 1",
                "y = 2",
                "# comment",
                "print(x)",
                "",
            ]
        ),
        max_size=12,
    )


@st.composite
def file_content_pairs(draw: st.DrawFn) -> tuple[str, str]:
    """An (old, new) pair of file contents for diff-classification tests.

    Sometimes the old content is empty (a new-file diff: all lines added), and
    sometimes the two share a common prefix so the diff exercises context lines.
    """
    old_lines = draw(_file_lines())
    if draw(st.booleans()):
        old_lines = []  # new-file case
    new_lines = list(old_lines)
    # Apply a few edits: insert / delete / modify.
    n_edits = draw(st.integers(min_value=0, max_value=4))
    for _ in range(n_edits):
        if new_lines and draw(st.booleans()):
            idx = draw(st.integers(min_value=0, max_value=len(new_lines) - 1))
            op = draw(st.sampled_from(["modify", "delete"]))
            if op == "delete":
                del new_lines[idx]
            else:
                new_lines[idx] = new_lines[idx] + "  # changed"
        else:
            insert_at = draw(st.integers(min_value=0, max_value=len(new_lines)))
            new_lines.insert(insert_at, draw(st.text(max_size=20)))
    return "\n".join(old_lines), "\n".join(new_lines)


# ---------------------------------------------------------------------------
# Call-record sequences (for cost/token accumulation)
# ---------------------------------------------------------------------------

def call_records() -> st.SearchStrategy[Dict[str, Any]]:
    """A single model-call cost/token record."""
    return st.fixed_dictionaries(
        {
            "prompt_tokens": st.integers(min_value=0, max_value=200_000),
            "completion_tokens": st.integers(min_value=0, max_value=200_000),
            "cost": st.floats(
                min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
            ),
        }
    )


def call_record_sequences(
    *, min_size: int = 0, max_size: int = 30
) -> st.SearchStrategy[List[Dict[str, Any]]]:
    """A sequence of per-call cost/token records for accumulation/threshold tests."""
    return st.lists(call_records(), min_size=min_size, max_size=max_size)
