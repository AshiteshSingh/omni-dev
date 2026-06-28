"""
Text Tool-Call Parser
======================

A small, isolated, pure module that extracts tool calls embedded in a model's
text *content* (as opposed to native function-calling) and strips those
tool-call structures back out so the user only sees human-readable text.

This replaces the inline balanced-brace JSON scanner + ``repair_json_string``
helper and the ``_clean_final_text`` band-aid that currently live in
``src/agent/core.py``. Keeping the logic here makes it unit- and
property-testable in isolation.

Two public functions:

- :func:`extract_tool_calls` — locate fenced ```json / ```tool blocks, explicit
  ``{"name": ..., "arguments": ...}`` objects, arrays of them, and blocks after
  a ``"Tool Calls:"`` marker, returning only calls whose ``name`` is in the
  provided ``valid_tools`` set. Never raises; returns ``[]`` on no match.
- :func:`strip_tool_call_text` — remove recognized tool-call blocks / fenced
  tool JSON / ``"Tool Calls:"`` sections so only legitimate prose (and normal,
  non-tool fenced code) remains. Returns ``""`` when the whole content was a
  tool-call blob (the loop decides what to show in that case).

Return shape
------------
:func:`extract_tool_calls` returns :class:`ParsedCall` instances. Each
``ParsedCall`` is drop-in compatible with the shape the agent loop already
consumes for native calls — it exposes ``.id`` and a ``.function`` namespace
with ``.name`` and ``.arguments`` (a JSON string) — while also exposing
``.name`` / ``.arguments`` directly for convenient testing. This avoids a
separate adapter: a ``ParsedCall`` can be used anywhere the loop previously used
``SimpleNamespace(id=..., function=SimpleNamespace(name=..., arguments=...))``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

__all__ = ["ParsedCall", "extract_tool_calls", "strip_tool_call_text"]


@dataclass(frozen=True)
class ParsedCall:
    """A tool call extracted from model text content.

    Drop-in compatible with the native tool-call shape used by the agent loop:
    ``call.id``, ``call.function.name`` and ``call.function.arguments`` all work,
    where ``arguments`` is always a JSON-encoded string.
    """

    id: str
    name: str
    arguments: str  # JSON-encoded string

    @property
    def function(self) -> SimpleNamespace:
        """Namespace mirroring the OpenAI/litellm tool-call ``function`` field."""
        return SimpleNamespace(name=self.name, arguments=self.arguments)


# Marker tokens some models wrap tool calls in, e.g. <tool_call> ... </tool_call>
# or <|tool_call|>. Used when stripping so leftover tags don't pollute prose.
_TOOL_CALL_MARKER_RE = re.compile(r"<\|?/?tool_call\|?>", re.IGNORECASE)
# An empty fence left behind after a tool-call JSON body is removed.
_EMPTY_FENCE_RE = re.compile(r"```(?:json|tool)?\s*```", re.IGNORECASE)
# A fence that explicitly tags tool content, e.g. ```tool ... ```
_TOOL_FENCE_RE = re.compile(r"```tool\b.*?```", re.IGNORECASE | re.DOTALL)


def _repair_json_string(s: str) -> str:
    """Best-effort repair of invalid backslash escapes in a JSON-ish string.

    Some local models emit Windows paths or LaTeX with lone backslashes that
    are not valid JSON escapes. We double any backslash that does not begin a
    legal escape so ``json.loads`` can parse it.
    """

    def escape_match(match: "re.Match[str]") -> str:
        text = match.group(0)
        if re.match(r'^\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})', text):
            return text
        return "\\\\" + text[1:]

    s_repaired = re.sub(r"\\.", escape_match, s)
    s_repaired = re.sub(r"\\$", r"\\\\", s_repaired)
    return s_repaired


def _safe_parse(block: str):
    """Parse a JSON block tolerantly. Returns the parsed value or ``None``."""
    try:
        return json.loads(block, strict=False)
    except Exception:
        pass
    try:
        return json.loads(_repair_json_string(block), strict=False)
    except Exception:
        return None


def _find_json_blocks(text: str) -> list[tuple[int, int, str]]:
    """Locate balanced ``{...}`` / ``[...]`` blocks in ``text``.

    Returns a list of ``(start, end, substring)`` for each outermost balanced
    block, respecting string literals and escapes so braces inside strings do
    not confuse the scanner. Unbalanced openers are skipped.
    """
    blocks: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "{[":
            start = i
            stack = [ch]
            in_str = False
            esc = False
            i += 1
            while i < n and stack:
                c = text[i]
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = not in_str
                elif not in_str:
                    if c in "{[":
                        stack.append(c)
                    elif c == "}":
                        if stack and stack[-1] == "{":
                            stack.pop()
                        else:
                            break
                    elif c == "]":
                        if stack and stack[-1] == "[":
                            stack.pop()
                        else:
                            break
                i += 1
            if not stack:
                blocks.append((start, i, text[start:i]))
            else:
                # Unbalanced — resume scanning just past this opener.
                i = start + 1
        else:
            i += 1
    return blocks


def _iter_call_dicts(parsed):
    """Yield candidate tool-call dicts from a parsed JSON value."""
    items = parsed if isinstance(parsed, list) else [parsed]
    for item in items:
        if isinstance(item, dict):
            fn = item.get("function", item)
            if isinstance(fn, dict):
                yield item, fn


def _is_tool_call_json(block: str) -> bool:
    """True if ``block`` parses to a tool-call object/array (has name+arguments)."""
    parsed = _safe_parse(block)
    if parsed is None:
        return False
    for _item, fn in _iter_call_dicts(parsed):
        if "name" in fn and "arguments" in fn:
            return True
    return False


def extract_tool_calls(content: str, valid_tools: set) -> list[ParsedCall]:
    """Extract tool calls embedded in model text ``content``.

    Locates fenced ```json / ```tool blocks, explicit ``{"name", "arguments"}``
    objects, arrays of them, and blocks following a ``"Tool Calls:"`` marker via
    a balanced-brace scanner with tolerant JSON parsing. Returns only calls whose
    ``name`` is present in ``valid_tools``. Never raises; returns ``[]`` when
    nothing matches. Deduplication is intentionally *not* performed here — the
    agent loop handles repeat detection.
    """
    if not content:
        return []
    try:
        valid = set(valid_tools) if valid_tools else set()
    except TypeError:
        valid = set()

    calls: list[ParsedCall] = []
    try:
        for _start, _end, block in _find_json_blocks(content):
            parsed = _safe_parse(block)
            if parsed is None:
                continue
            for item, fn in _iter_call_dicts(parsed):
                name = fn.get("name")
                if not isinstance(name, str) or name not in valid:
                    continue
                args = fn.get("arguments", {})
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args)
                    except Exception:
                        args = "{}"
                raw_id = item.get("id") if isinstance(item, dict) else None
                call_id = str(raw_id) if raw_id else f"call_{uuid.uuid4().hex[:8]}"
                calls.append(ParsedCall(id=call_id, name=name, arguments=args))
    except Exception:
        # Parsing must never raise into the loop; return whatever we gathered.
        return calls
    return calls


def strip_tool_call_text(content: str) -> str:
    """Remove recognized tool-call structures, leaving only human-readable text.

    Removes embedded tool-call JSON objects/arrays, fenced ```tool blocks, the
    ``"Tool Calls:"`` section, and tool-call marker tags. Legitimate prose and
    normal (non-tool) fenced code blocks are preserved (Requirement 3.7). If the
    entire content was a tool-call blob, returns ``""`` so the loop can decide
    what to display. Never raises.
    """
    if not content:
        return content or ""

    try:
        text = content

        # 1. Drop an explicit "Tool Calls:" section — everything from the marker on.
        if "Tool Calls:" in text:
            text = text.split("Tool Calls:", 1)[0]

        # 2. Remove fenced ```tool blocks outright (these always denote tool calls).
        text = _TOOL_FENCE_RE.sub("", text)

        # 3. Blank out any balanced JSON block that is a tool call. Iterate from the
        #    end so earlier spans keep their indices. Non-tool JSON (and normal code
        #    fences whose body is not a tool call) are left untouched.
        blocks = _find_json_blocks(text)
        for start, end, block in sorted(blocks, key=lambda b: b[0], reverse=True):
            if _is_tool_call_json(block):
                text = text[:start] + text[end:]

        # 4. Remove tool-call marker tags and any now-empty fences left behind.
        text = _TOOL_CALL_MARKER_RE.sub("", text)
        text = _EMPTY_FENCE_RE.sub("", text)

        return text.strip()
    except Exception:
        # On any unexpected failure, fall back to the original content rather
        # than crashing the render path.
        return content
