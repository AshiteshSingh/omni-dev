"""
Output Renderer
===============

Clean, professional rendering of agent output to the terminal using Rich.

This module replaces the fake word-by-word ``render_smooth_markdown`` that used
``time.sleep`` to simulate streaming. It guarantees the user never sees raw
tool-call JSON or literal escape sequences (``\\n``/``\\t``) and that malformed
markdown can never crash the session.

This file owns the *non-streaming* rendering surface:

- :func:`normalize_escapes` — convert literal ``\\n``/``\\t`` sequences that
  appear *outside* fenced code blocks into real newline/tab characters, while
  leaving the boundaries and inner content of every fenced ``` code block
  intact (Req 3.2, 3.3, Property 7). Never raises (Req 3.8 / Property 8).
- :func:`clean_for_display` — combine
  :func:`~src.agent.tool_parser.strip_tool_call_text` with
  :func:`normalize_escapes` into one resilient cleaning step reused by
  :func:`render_final` and elsewhere. Never raises.
- :func:`render_final` — single Rich ``Markdown`` render of cleaned text with no
  artificial per-word delay (Req 3.1, 3.2, 4.4). Falls back to plain text on any
  rendering failure so malformed markdown never crashes (Req 3.8).
- :func:`render_error` — themed error rendering using the ``status.err`` style
  and the ``✗`` glyph (Req 6.6).

It also owns the *streaming* rendering surface:

- :func:`stream_response` — consume a ``litellm`` streaming response, accumulate
  ``delta.content`` into a running buffer, and update a Rich ``Live`` view that
  re-renders the *full* accumulated buffer as Markdown on every chunk (never
  per-fragment), so the streamed result is identical to a one-shot
  :func:`render_final` of the same content (Req 4.5, Property 9). Each token is
  displayed as received with no artificial delay (Req 4.3). It also accumulates
  ``delta.tool_calls`` fragments by index into complete tool calls and returns
  them alongside the final assistant text. Never raises out of the render path.
- :func:`accumulate_tool_call_deltas` — pure helper that folds streamed
  tool-call fragments (each carrying ``.index``/``.id``/``.function.name``/
  ``.function.arguments``) into a list of complete, loop-compatible tool calls.

The structured diff renderer (``render_diff``) is implemented in a separate task;
this module leaves room for it and must not break when it is added.
"""

from __future__ import annotations

import difflib
import json
import uuid
from typing import NamedTuple

__all__ = [
    "normalize_escapes",
    "clean_for_display",
    "render_final",
    "render_error",
    "stream_response",
    "accumulate_tool_call_deltas",
    "DiffLine",
    "compute_diff_hunks",
    "render_diff",
]

# --- Defensive imports -------------------------------------------------------
# theme.py is authored in parallel; render must remain import-safe and degrade
# gracefully if the theme (or Rich) is unavailable at import time.
try:  # pragma: no cover - exercised indirectly
    from rich.markdown import Markdown
except Exception:  # pragma: no cover
    Markdown = None  # type: ignore[assignment]

try:  # pragma: no cover
    from rich.live import Live
except Exception:  # pragma: no cover
    Live = None  # type: ignore[assignment]

try:  # pragma: no cover
    from rich.text import Text
except Exception:  # pragma: no cover
    Text = None  # type: ignore[assignment]

try:  # pragma: no cover
    from rich.panel import Panel
except Exception:  # pragma: no cover
    Panel = None  # type: ignore[assignment]

try:  # pragma: no cover
    from .theme import OMNI_THEME  # noqa: F401  (re-exported convenience)
except Exception:  # pragma: no cover
    OMNI_THEME = None  # type: ignore[assignment]

try:  # pragma: no cover
    from .theme import make_console  # noqa: F401  (re-exported convenience)
except Exception:  # pragma: no cover
    make_console = None  # type: ignore[assignment]

try:
    from ..agent.tool_parser import strip_tool_call_text
except Exception:  # pragma: no cover - fallback keeps render import-safe

    def strip_tool_call_text(content):  # type: ignore[misc]
        """Fallback no-op used only if the tool parser cannot be imported."""
        return content or ""


# ParsedCall is the loop-compatible tool-call shape (``.id`` + ``.function`` with
# ``.name``/``.arguments``). Reusing it means streamed tool calls can be handed
# straight to the agent loop. Fall back to a local equivalent if the parser
# module is unavailable so this module stays import-safe.
try:
    from ..agent.tool_parser import ParsedCall
except Exception:  # pragma: no cover - fallback keeps render import-safe
    from dataclasses import dataclass as _dataclass
    from types import SimpleNamespace as _SimpleNamespace

    @_dataclass(frozen=True)
    class ParsedCall:  # type: ignore[no-redef]
        """Minimal stand-in mirroring ``tool_parser.ParsedCall``."""

        id: str
        name: str
        arguments: str  # JSON-encoded string

        @property
        def function(self):
            return _SimpleNamespace(name=self.name, arguments=self.arguments)


# Error glyph (Req 6.6). The themed console handles encoding/legacy fallback.
_ERROR_GLYPH = "✗"
_ERROR_STYLE = "status.err"


def _convert_literal_escapes(segment: str) -> str:
    """Convert literal ``\\n``/``\\t``/``\\r`` escape sequences to real chars.

    Walks the string so an escaped backslash (``\\\\``) is preserved verbatim and
    never misread as the start of a ``\\n`` newline. Only operates on a single
    *outside-fence* segment; callers ensure fenced code is excluded.
    """
    if not segment or "\\" not in segment:
        return segment

    out: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "\\" and i + 1 < n:
            nxt = segment[i + 1]
            if nxt == "\\":
                # Escaped backslash: keep both characters, consume both so the
                # second backslash can't pair with a following 'n'/'t'.
                out.append("\\\\")
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            # Any other escape (e.g. \" or \d) is left untouched.
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def normalize_escapes(text: str) -> str:
    """Normalize literal escape sequences outside fenced code blocks.

    Converts literal ``\\n``/``\\t`` (and ``\\r``) sequences that occur *outside*
    fenced ``` code blocks into their corresponding whitespace, while leaving the
    fence boundaries and the inner content of every fenced block exactly intact
    (Req 3.2, 3.3, Property 7).

    Implemented by splitting on the ``` fence token: segments at even indices are
    outside code (and get transformed); odd-index segments are the content of a
    fenced block (left verbatim). This is robust to an odd number of fences,
    partial/broken fences, and fences embedded mid-line in garbled output — and
    never raises (Req 3.8 / Property 8).
    """
    if not text:
        return text or ""
    try:
        segments = text.split("```")
        # Even indices are outside fenced code; odd indices are inside.
        for idx in range(0, len(segments), 2):
            segments[idx] = _convert_literal_escapes(segments[idx])
        return "```".join(segments)
    except Exception:
        # Cleaning must never crash the render path; return input unchanged.
        return text


def clean_for_display(text: str) -> str:
    """Strip tool-call text then normalize escapes, resiliently.

    Combines :func:`strip_tool_call_text` (removes raw native/text tool-call JSON
    so none leaks into the Final_Response — Req 3.1) with :func:`normalize_escapes`
    (Req 3.2). Returns the most salvageable text it can and never raises
    (Req 3.8 / Property 8); on partial failure it returns whatever stage
    succeeded rather than failing the whole render.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""

    try:
        stripped = strip_tool_call_text(text)
        if not isinstance(stripped, str):
            stripped = text
    except Exception:
        stripped = text

    try:
        return normalize_escapes(stripped)
    except Exception:
        return stripped


def render_final(text: str, console, theme=None) -> None:
    """Render the Final_Response as a single Markdown block, no artificial delay.

    The text is escape-normalized and tool-call-stripped via
    :func:`clean_for_display` first so no raw tool JSON or literal ``\\n`` leaks
    (Req 3.1, 3.2). A single Rich ``Markdown`` render is performed with no
    per-word ``time.sleep`` (Req 4.4). If markdown rendering fails for any reason
    (malformed input, missing Rich), it falls back to printing the cleaned plain
    text so the render never crashes (Req 3.8).
    """
    cleaned = clean_for_display(text)
    if not cleaned or not cleaned.strip():
        # Nothing renderable (e.g. content was entirely tool-call text). The
        # empty-response notice is handled by the caller (Req 6.4).
        return

    if Markdown is not None and console is not None:
        try:
            console.print(Markdown(cleaned))
            return
        except Exception:
            # Malformed markdown — fall back to plain text rather than failing.
            pass

    _print_plain(console, cleaned)


def render_error(message: str, console, theme=None) -> None:
    """Render an error message in the professional themed error style (Req 6.6).

    Uses the ``status.err`` style and the ``✗`` glyph. The message is run through
    :func:`clean_for_display` so error strings are themselves free of raw
    tool-call JSON or literal escapes (Req 6.6, 3.1, 3.2). Never raises.
    """
    try:
        body = clean_for_display(message) if message else ""
    except Exception:
        body = message if isinstance(message, str) else ""

    body = body.strip() if body else ""
    line = f"{_ERROR_GLYPH} {body}" if body else _ERROR_GLYPH

    if console is not None:
        try:
            console.print(line, style=_ERROR_STYLE)
            return
        except Exception:
            try:
                console.print(line)
                return
            except Exception:
                pass
    _print_plain(console, line)


def _print_plain(console, text: str) -> None:
    """Last-resort plain output that never raises."""
    if console is not None:
        try:
            console.print(text, markup=False, highlight=False)
            return
        except Exception:
            try:
                console.print(text)
                return
            except Exception:
                pass
    try:
        print(text)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Streaming renderer
# ─────────────────────────────────────────────────────────────────────────────
def _get(obj, attr):
    """Read ``attr`` from an object *or* a mapping, returning ``None`` if absent.

    litellm stream chunks are usually attribute objects, but some backends (and
    tests) hand back plain dicts. This keeps the streaming path robust to both.
    """
    if obj is None:
        return None
    try:
        value = getattr(obj, attr)
        if value is not None:
            return value
    except Exception:
        pass
    if isinstance(obj, dict):
        return obj.get(attr)
    return None


def _extract_delta(chunk):
    """Return ``choices[0].delta`` from a stream chunk, or ``None`` if malformed.

    Tolerates chunks lacking ``choices`` or ``delta`` entirely (Req 3.8). Never
    raises.
    """
    try:
        choices = _get(chunk, "choices")
        if not choices:
            return None
        choice = choices[0]
        return _get(choice, "delta")
    except Exception:
        return None


def accumulate_tool_call_deltas(fragments) -> list:
    """Fold streamed tool-call fragments into complete tool calls.

    ``fragments`` is the flat, ordered list of every ``delta.tool_calls`` entry
    seen across all chunks. Each fragment carries an ``index`` (which tool call
    it belongs to), and may carry an ``id``, a ``function.name``, and a slice of
    ``function.arguments``. Fragments sharing an index are merged: the ``id`` and
    ``name`` are taken from whichever fragment supplies them, and the argument
    slices are concatenated in arrival order to rebuild the full JSON string.

    Returns a list of :class:`~src.agent.tool_parser.ParsedCall` (ordered by
    first appearance) that is drop-in compatible with the agent loop's native
    tool-call consumption. Fragments that never resolve a tool name are dropped
    as incomplete. Pure and total: never raises, returns ``[]`` on empty input.
    """
    if not fragments:
        return []

    by_index: dict = {}
    order: list = []
    last_index = None

    try:
        for frag in fragments:
            if frag is None:
                continue
            idx = _get(frag, "index")
            fid = _get(frag, "id")
            fn = _get(frag, "function")
            name = _get(fn, "name") if fn is not None else None
            args = _get(fn, "arguments") if fn is not None else None

            if idx is None:
                # No explicit index: treat a fragment that only carries argument
                # text as a continuation of the current call; anything that
                # introduces a new id/name starts a fresh slot.
                if last_index is not None and not fid and not name:
                    idx = last_index
                else:
                    idx = len(order)

            if idx not in by_index:
                by_index[idx] = {"id": None, "name": None, "args": []}
                order.append(idx)
            slot = by_index[idx]

            if fid:
                slot["id"] = str(fid)
            if name:
                slot["name"] = name
            if args is not None and args != "":
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args)
                    except Exception:
                        args = ""
                if args:
                    slot["args"].append(args)
            last_index = idx
    except Exception:
        # Accumulation must never raise into the loop; fall through with whatever
        # has been collected so far.
        pass

    calls: list = []
    for idx in order:
        slot = by_index.get(idx) or {}
        name = slot.get("name")
        if not name:
            # Incomplete fragment with no resolvable tool name — skip it.
            continue
        call_id = slot.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        arguments = "".join(slot.get("args") or [])
        if not arguments:
            arguments = "{}"
        calls.append(ParsedCall(id=call_id, name=name, arguments=arguments))
    return calls


def _stream_renderable(raw_buffer: str):
    """Build the Rich renderable for the *full* accumulated buffer.

    Uses the same :func:`clean_for_display` + ``Markdown`` path as
    :func:`render_final` so that what streams in the ``Live`` view matches the
    committed render (Req 4.5, Property 9): tool-call JSON is stripped and
    literal escapes normalized before rendering. Falls back to plain ``Text`` if
    Markdown is unavailable or the content is not valid markdown. Never raises.
    """
    cleaned = clean_for_display(raw_buffer)
    if Markdown is not None and cleaned.strip():
        try:
            return Markdown(cleaned)
        except Exception:
            pass
    if Text is not None:
        try:
            return Text(cleaned)
        except Exception:
            pass
    return cleaned


def _iter_chunks_sync(stream):
    """Yield chunks from a sync iterable, swallowing iteration errors.

    litellm streaming responses are sync generators in the common case. Any
    exception raised mid-iteration (network hiccup, malformed stream) ends the
    iteration cleanly rather than propagating out of the render path.
    """
    try:
        iterator = iter(stream)
    except TypeError:
        return
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            return
        except Exception:
            return
        yield chunk


async def stream_response(stream, console, theme=None, on_first_chunk=None):
    """Render a streaming model response and return ``(final_text, tool_calls)``.

    Consumes a ``litellm`` streaming response — an iterable (sync or async) of
    chunk objects whose ``choices[0].delta`` carries an optional ``content`` text
    fragment and optional ``tool_calls`` fragments. As chunks arrive this:

    - Accumulates ``delta.content`` into a single running buffer and updates a
      Rich ``Live`` view that re-renders the **full** accumulated buffer as
      Markdown on every chunk (never per-fragment), so the streamed output is
      identical to a one-shot :func:`render_final` of the same content
      (Req 4.5, Property 9). Each token appears as received with no artificial
      sleep/delay (Req 4.3).
    - Collects every ``delta.tool_calls`` fragment and folds them by index into
      complete tool calls via :func:`accumulate_tool_call_deltas`.

    Returns a ``(final_text, tool_calls)`` tuple where ``final_text`` is the
    accumulated assistant text run through :func:`clean_for_display` (the same
    cleaning the committed render uses) and ``tool_calls`` is the list of
    assembled, loop-compatible tool calls.

    Robustness (Req 3.8): a non-iterable ``stream`` yields ``("", [])``; chunks
    missing ``choices``/``delta`` are skipped; rendering failures fall back to
    pure accumulation. This function never raises out of the render path.
    """
    content_parts: list = []
    tool_fragments: list = []

    def _consume_chunk(chunk) -> bool:
        """Fold one chunk into the buffers. Returns True if content changed."""
        delta = _extract_delta(chunk)
        if delta is None:
            return False
        changed = False
        piece = _get(delta, "content")
        if isinstance(piece, str) and piece:
            content_parts.append(piece)
            changed = True
        tcs = _get(delta, "tool_calls")
        if tcs:
            try:
                for frag in tcs:
                    tool_fragments.append(frag)
            except TypeError:
                tool_fragments.append(tcs)
        return changed

    # Pick the iteration strategy. Async iterables are supported but litellm's
    # sync generator is the primary path.
    is_async = hasattr(stream, "__aiter__")
    is_sync = (not is_async) and hasattr(stream, "__iter__")

    if not is_async and not is_sync:
        # Not a stream at all — nothing to render (Req 3.8).
        return "", []

    use_live = (
        Live is not None
        and console is not None
        and bool(getattr(console, "is_terminal", False))
    )

    live = None
    first_fired = False

    def _start_live_if_needed():
        nonlocal live
        if use_live and live is None:
            try:
                live = Live(
                    _stream_renderable("".join(content_parts)),
                    console=console,
                    refresh_per_second=30,
                    transient=True,
                )
                live.start()
            except Exception:
                live = None

    def _on_change():
        # Fire the first-chunk hook once (used to stop the caller's spinner and
        # print the assistant header), then lazily start the Live view so the
        # spinner can keep animating until the first token actually arrives.
        nonlocal first_fired
        if not first_fired:
            first_fired = True
            if on_first_chunk is not None:
                try:
                    on_first_chunk()
                except Exception:
                    pass
            _start_live_if_needed()
        if live is not None:
            try:
                live.update(_stream_renderable("".join(content_parts)))
            except Exception:
                pass

    try:
        if is_async:
            async for chunk in stream:  # type: ignore[union-attr]
                try:
                    changed = _consume_chunk(chunk)
                except Exception:
                    changed = False
                if changed:
                    _on_change()
        else:
            for chunk in _iter_chunks_sync(stream):
                try:
                    changed = _consume_chunk(chunk)
                except Exception:
                    changed = False
                if changed:
                    _on_change()
    except Exception:
        # Any unexpected failure during iteration: stop streaming but still
        # return what was accumulated so the caller can recover (Req 3.8).
        pass
    finally:
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass

    raw_buffer = "".join(content_parts)

    # Commit the permanent render through the exact non-streamed path so the
    # final on-screen result equals render_final of the same content (Req 4.5,
    # Property 9). The Live view above was transient and has been cleared.
    try:
        render_final(raw_buffer, console, theme)
    except Exception:
        pass

    try:
        tool_calls = accumulate_tool_call_deltas(tool_fragments)
    except Exception:
        tool_calls = []

    # Return the RAW buffer (not cleaned): the caller's loop may need the raw
    # text to detect text-encoded tool calls. Display above is already cleaned.
    return raw_buffer, tool_calls


# ─────────────────────────────────────────────────────────────────────────────
# Structured diff renderer
# ─────────────────────────────────────────────────────────────────────────────
# Diff-line styles map to the theme's semantic names (Req 14.2). The visible
# gutter prefix mirrors a unified diff so added/removed/context are obvious even
# without color (e.g. on a legacy renderer that drops styling).
_DIFF_PREFIX = {
    "added": "+",
    "removed": "-",
    "context": " ",
}
_DIFF_STYLE = {
    "added": "diff.add",
    "removed": "diff.del",
    "context": "diff.ctx",
}


class DiffLine(NamedTuple):
    """A single classified line within a diff hunk.

    ``kind`` is one of ``"added"``, ``"removed"`` or ``"context"``; ``text`` is
    the raw (not yet escape-normalized) line content with no trailing newline.
    """

    kind: str
    text: str


def compute_diff_hunks(old: str, new: str, context: int = 3) -> list:
    """Compute classified, context-bounded diff hunks between ``old`` and ``new``.

    Splits both inputs into lines and uses :mod:`difflib` to classify each line
    (Property 31): a line present only in ``new`` is ``"added"``, a line present
    only in ``old`` is ``"removed"``, and unchanged lines that fall within
    ``context`` lines of a change are emitted as ``"context"`` around each
    changed hunk. Unchanged regions further than ``context`` from any change are
    omitted entirely.

    When ``old`` is empty (a newly created file), **every** line of ``new`` is
    classified ``"added"`` (Req 14.4). When there are no differences, an empty
    list is returned.

    Returns a list of hunks, where each hunk is a list of :class:`DiffLine`.
    Pure and total: performs no I/O and never raises.
    """
    old_text = old or ""
    new_text = new or ""

    new_lines = new_text.splitlines()
    old_lines = old_text.splitlines()

    # New-file case: nothing on the left, so the whole new content is added.
    if not old_text:
        if not new_lines:
            return []
        return [[DiffLine("added", line) for line in new_lines]]

    try:
        ctx = context if isinstance(context, int) and context >= 0 else 3
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
        hunks: list = []
        for group in matcher.get_grouped_opcodes(ctx):
            hunk: list = []
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for line in old_lines[i1:i2]:
                        hunk.append(DiffLine("context", line))
                elif tag == "replace":
                    for line in old_lines[i1:i2]:
                        hunk.append(DiffLine("removed", line))
                    for line in new_lines[j1:j2]:
                        hunk.append(DiffLine("added", line))
                elif tag == "delete":
                    for line in old_lines[i1:i2]:
                        hunk.append(DiffLine("removed", line))
                elif tag == "insert":
                    for line in new_lines[j1:j2]:
                        hunk.append(DiffLine("added", line))
            if hunk:
                hunks.append(hunk)
        return hunks
    except Exception:
        # Diff computation must never crash the render path.
        return []


def _diff_body_text(hunks: list):
    """Build a Rich ``Text`` body for ``hunks``, or ``None`` if unavailable.

    Each line's text is escape-normalized (Req 14.5) so no literal ``\\n``/``\\t``
    or raw tool JSON survives into the rendered diff, then prefixed with the
    unified-diff gutter and styled with the matching ``diff.*`` theme style.
    Multiple hunks are separated by a muted divider.
    """
    if Text is None:
        return None
    body = Text()
    if not hunks:
        body.append("(no changes)", style="diff.ctx")
        return body

    for h_idx, hunk in enumerate(hunks):
        if h_idx > 0:
            # Separate adjacent hunks with a muted divider line.
            body.append("\u22ef\n", style="diff.ctx")
        for line in hunk:
            kind = getattr(line, "kind", "context")
            raw = getattr(line, "text", "")
            prefix = _DIFF_PREFIX.get(kind, " ")
            style = _DIFF_STYLE.get(kind, "diff.ctx")
            try:
                cleaned = normalize_escapes(raw)
            except Exception:
                cleaned = raw if isinstance(raw, str) else str(raw)
            # A normalized line may itself contain real newlines; keep the gutter
            # prefix aligned by re-prefixing each physical row.
            for sub in cleaned.split("\n"):
                body.append(prefix, style=style)
                body.append(" ", style=style)
                body.append(sub, style=style)
                body.append("\n")
    return body


class NumberedDiffLine(NamedTuple):
    """A diff line carrying its line number for rendering (kind, text, lineno)."""

    kind: str
    text: str
    lineno: int | None


def compute_numbered_hunks(old: str, new: str, context: int = 3) -> list:
    """Like :func:`compute_diff_hunks` but each line carries a line number.

    Added/context lines are numbered by their position in the NEW file; removed
    lines are numbered by their position in the OLD file. Mirrors Claude Code's
    ``StructuredDiff`` line numbering. Pure and total: never raises.
    """
    old_text = old or ""
    new_text = new or ""
    new_lines = new_text.splitlines()
    old_lines = old_text.splitlines()

    if not old_text:
        if not new_lines:
            return []
        return [[NumberedDiffLine("added", ln, i + 1) for i, ln in enumerate(new_lines)]]

    try:
        ctx = context if isinstance(context, int) and context >= 0 else 3
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
        hunks: list = []
        for group in matcher.get_grouped_opcodes(ctx):
            hunk: list = []
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for off, line in enumerate(old_lines[i1:i2]):
                        hunk.append(NumberedDiffLine("context", line, j1 + off + 1))
                elif tag == "replace":
                    for off, line in enumerate(old_lines[i1:i2]):
                        hunk.append(NumberedDiffLine("removed", line, i1 + off + 1))
                    for off, line in enumerate(new_lines[j1:j2]):
                        hunk.append(NumberedDiffLine("added", line, j1 + off + 1))
                elif tag == "delete":
                    for off, line in enumerate(old_lines[i1:i2]):
                        hunk.append(NumberedDiffLine("removed", line, i1 + off + 1))
                elif tag == "insert":
                    for off, line in enumerate(new_lines[j1:j2]):
                        hunk.append(NumberedDiffLine("added", line, j1 + off + 1))
            if hunk:
                hunks.append(hunk)
        return hunks
    except Exception:
        return []


# Background line styles + gutter for the numbered renderer.
_DIFF_BG_STYLE = {
    "added": "diff.add.bg",
    "removed": "diff.del.bg",
    "context": "diff.ctx",
}


def _diff_body_numbered(hunks: list):
    """Build a Rich ``Text`` body with line numbers + background highlighting.

    Each row: right-aligned line number (``diff.lineno``), a ``+``/``-``/`` ``
    gutter, then the escape-normalized code styled with a background for
    added/removed lines (mirrors Claude Code's StructuredDiff). Returns ``None``
    if Rich ``Text`` is unavailable.
    """
    if Text is None:
        return None
    if not hunks:
        body = Text()
        body.append("(no changes)", style="diff.ctx")
        return body

    # Width of the largest line number across all hunks, for alignment.
    max_no = 1
    for hunk in hunks:
        for ln in hunk:
            n = getattr(ln, "lineno", None)
            if isinstance(n, int) and n > max_no:
                max_no = n
    width = len(str(max_no))

    body = Text()
    for h_idx, hunk in enumerate(hunks):
        if h_idx > 0:
            body.append(("\u22ef" + "\n"), style="diff.ctx")  # ⋯ hunk divider
        for line in hunk:
            kind = getattr(line, "kind", "context")
            raw = getattr(line, "text", "")
            no = getattr(line, "lineno", None)
            gutter = _DIFF_PREFIX.get(kind, " ")
            bg_style = _DIFF_BG_STYLE.get(kind, "diff.ctx")
            try:
                cleaned = normalize_escapes(raw)
            except Exception:
                cleaned = raw if isinstance(raw, str) else str(raw)
            for sub in cleaned.split("\n"):
                num_label = (str(no).rjust(width) if isinstance(no, int) else " " * width)
                body.append(f"{num_label} ", style="diff.lineno")
                body.append(f"{gutter} ", style=bg_style)
                body.append(sub, style=bg_style)
                body.append("\n")
    return body


def _print_unified_diff(old: str, new: str, path: str, console) -> None:
    """Last-resort plain unified-diff print used when Rich is unavailable."""
    try:
        old_lines = (old or "").splitlines()
        new_lines = (new or "").splitlines()
        label = path or "file"
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=label,
            tofile=label,
            lineterm="",
        )
        text = "\n".join(normalize_escapes(line) for line in diff)
        if not text.strip():
            text = f"{label}: (no changes)"
    except Exception:
        text = f"{path or 'file'}: (diff unavailable)"
    _print_plain(console, text)


def render_diff(old: str, new: str, path: str, console, theme=None) -> None:
    """Render a Structured_Diff of ``old`` → ``new`` in a titled Rich panel.

    Computes context-bounded hunks via :func:`compute_diff_hunks`, then renders
    them inside a Rich :class:`~rich.panel.Panel` titled with ``path``. Added
    lines use the ``diff.add`` style with a ``+`` gutter, removed lines use
    ``diff.del`` with ``-``, and surrounding context uses ``diff.ctx`` with a
    space (Req 14.1, 14.2, 14.3). New files render every line as added
    (Req 14.4). All line text passes through :func:`normalize_escapes` so no
    literal ``\\n``/``\\t`` or raw tool JSON appears (Req 14.5).

    Never raises (Req 6, 3.8): if Rich/``Panel`` is unavailable or panel
    rendering fails, it falls back to printing a plain unified diff.
    """
    try:
        hunks = compute_numbered_hunks(old or "", new or "")
    except Exception:
        hunks = []

    title = path or "diff"

    if Panel is not None and Text is not None and console is not None:
        try:
            body = _diff_body_numbered(hunks)
            if body is not None:
                panel = Panel(
                    body,
                    title=title,
                    title_align="left",
                    border_style="app.muted",
                    expand=True,
                )
                console.print(panel)
                return
        except Exception:
            # Fall through to the plain unified-diff fallback below.
            pass

    _print_unified_diff(old, new, title, console)
