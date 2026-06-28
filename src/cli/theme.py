"""
theme.py - Cohesive visual system for the Omni-Dev CLI.

This module is the single source of style for the interface. It centralizes:

- ``OMNI_THEME``: a Rich ``Theme`` mapping semantic style names (not raw colors)
  so the palette can change in one place and stay consistent (Req 4.2).
- ``make_console``: builds a Rich ``Console`` wired to the theme with the right
  Windows-safe settings (``legacy_windows=False``, ``force_terminal``).
- A glyph vocabulary with UTF-8 glyphs and ASCII fallbacks, selected based on
  the renderer in use (legacy Windows console or the ``OMNI_ASCII`` env flag).
- ``format_tool_activity``: the SINGLE code path that turns any tool invocation
  into a consistently styled activity line (Req 4.1, 4.2). This replaces the
  ad-hoc ``markers`` dict and inline ``[READ]``/``[CMD]`` markers in
  ``interface.py``.
- Message-framing helpers (turn headers, gutters, separators), a compact
  ``banner``, and a ``status_footer`` line.
- ``enforce_utf8``: centralized Windows UTF-8 enforcement consistent with the
  logic that previously lived in ``omni_dev.py`` / ``interface.py``.

It is intentionally import-safe: importing this module performs no I/O and has
no side effects. ``enforce_utf8`` must be called explicitly by the entry point.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Mapping, Optional

from rich.console import Console, Group, RenderableType
from rich.text import Text
from rich.theme import Theme

# ─────────────────────────────────────────────────────────────────────────────
# Theme & typographic hierarchy
# ─────────────────────────────────────────────────────────────────────────────
# Semantic style names used everywhere in the interface. A restrained dark
# palette: one accent, a muted secondary, and semantic success/warn/error.
OMNI_THEME = Theme(
    {
        "app.banner": "bold #7C9CF0",
        "app.accent": "#7C9CF0",          # primary accent (assistant)
        "app.muted": "dim #8A8F98",       # secondary text, separators
        "user.gutter": "#5A6270",
        "assistant.gutter": "#7C9CF0",
        "tool.run": "#E2B341",            # command/run activity
        "tool.read": "#56B6C2",           # read activity
        "tool.edit": "#C586C0",           # edit/write activity
        "status.ok": "bold #98C379",
        "status.warn": "bold #E5C07B",
        "status.err": "bold #E06C75",
        "diff.add": "#98C379",
        "diff.del": "#E06C75",
        "diff.ctx": "dim #8A8F98",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Windows UTF-8 handling (centralized)
# ─────────────────────────────────────────────────────────────────────────────
def enforce_utf8() -> None:
    """Enforce UTF-8 terminal I/O on Windows.

    Centralizes the UTF-8 enforcement previously duplicated in ``omni_dev.py``
    and ``interface.py``: set the console code page to 65001, export the
    ``PYTHONUTF8`` / ``PYTHONIOENCODING`` hints, and re-wrap stdout/stderr in
    UTF-8 ``TextIOWrapper``s (with ``errors="replace"``) when they are not
    already UTF-8. This prevents the box-drawing/encoding glitches that produce
    garbled output. A no-op on non-Windows platforms.

    Safe to call more than once.
    """
    if sys.platform != "win32":
        return

    # Best-effort: switch the active code page to UTF-8.
    try:
        os.system("chcp 65001 > nul 2>&1")
    except Exception:
        pass

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        enc = (getattr(stream, "encoding", "") or "").lower()
        if enc in ("utf-8", "utf8"):
            continue
        import io

        setattr(
            sys,
            name,
            io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Console factory
# ─────────────────────────────────────────────────────────────────────────────
def make_console(**overrides: Any) -> Console:
    """Build a Rich ``Console`` configured with :data:`OMNI_THEME`.

    ``force_terminal`` bypasses the Windows legacy renderer, ``legacy_windows``
    is disabled so modern box-drawing/glyphs render cleanly, and ``highlight``
    is off so we control all styling through the theme. Any keyword override is
    forwarded to the ``Console`` constructor.
    """
    kwargs: dict[str, Any] = dict(
        theme=OMNI_THEME,
        force_terminal=True,
        legacy_windows=False,
        highlight=False,
    )
    kwargs.update(overrides)
    return Console(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Glyphs (UTF-8 + ASCII fallbacks)
# ─────────────────────────────────────────────────────────────────────────────
# Semantic glyph keys. Activity glyphs come from the design's glyph table;
# framing glyphs back the message-framing helpers below.
GLYPHS_UTF8: dict[str, str] = {
    "reading": "\u25c7",    # ◇
    "searching": "\u25c8",  # ◈
    "running": "\u25b8",    # ▸
    "editing": "\u270e",    # ✎
    "thinking": "\u273b",   # ✻
    "done": "\u2713",       # ✓
    "error": "\u2717",      # ✗
    # framing
    "bar": "\u2502",        # │
    "turn_end": "\u2570\u2500",  # ╰─
    "sep": "\u00b7",        # ·  (status-footer separator)
    "branch": "\u2387",     # ⎇  (status-footer git branch marker)
}

GLYPHS_ASCII: dict[str, str] = {
    "reading": "<>",
    "searching": "[]",
    "running": ">",
    "editing": "*",
    "thinking": "*",
    "done": "OK",
    "error": "x",
    # framing
    "bar": "|",
    "turn_end": "`-",
    "sep": "-",
    "branch": "git:",
}


def should_use_ascii(console: Optional[Console] = None) -> bool:
    """Return ``True`` when ASCII fallback glyphs should be used.

    Detection order:
    1. The ``OMNI_ASCII`` environment flag (truthy values force ASCII).
    2. A legacy Windows renderer (``console.legacy_windows``).
    """
    flag = (os.environ.get("OMNI_ASCII", "") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if console is not None and getattr(console, "legacy_windows", False):
        return True
    return False


def glyphs_for(console: Optional[Console] = None) -> dict[str, str]:
    """Return the active glyph map (UTF-8 or ASCII) for ``console``."""
    return GLYPHS_ASCII if should_use_ascii(console) else GLYPHS_UTF8


def glyph(name: str, console: Optional[Console] = None) -> str:
    """Return a single glyph by semantic ``name`` for the active renderer."""
    return glyphs_for(console).get(name, "")


# ─────────────────────────────────────────────────────────────────────────────
# Tool-activity lines (single code path)
# ─────────────────────────────────────────────────────────────────────────────
def _truncate(value: Any, limit: int = 72) -> str:
    """Render ``value`` as a single-line string, truncated with an ellipsis."""
    text = "" if value is None else str(value)
    # Collapse newlines so an activity line stays on one row.
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "\u2026"
    return text


# Each entry maps a tool name to (glyph_key, style, label_builder). The label
# builder receives the tool's argument dict and returns a concise label. This
# is the ONE place tool presentation is defined (Req 4.2).
_TOOL_ACTIVITY: dict[str, tuple[str, str, Any]] = {
    "read_file": ("reading", "tool.read", lambda a: f"Reading file  {_truncate(a.get('path', ''))}"),
    "read_notebook": ("reading", "tool.read", lambda a: f"Reading notebook  {_truncate(a.get('path', ''))}"),
    "read_url_content": ("reading", "tool.read", lambda a: f"Fetching  {_truncate(a.get('url', ''))}"),
    "list_dir": ("reading", "tool.read", lambda a: f"Listing  {_truncate(a.get('path', '.'))}"),
    "recall": ("reading", "tool.read", lambda a: f"Recalling  {_truncate(a.get('query', ''))}"),
    "search_codebase": ("searching", "tool.read", lambda a: f"Searching  \"{_truncate(a.get('pattern', ''))}\""),
    "glob_files": ("searching", "tool.read", lambda a: f"Finding files  {_truncate(a.get('pattern', ''))}"),
    "search_web": ("searching", "tool.read", lambda a: f"Web search  \"{_truncate(a.get('query', ''))}\""),
    "run_command": ("running", "tool.run", lambda a: f"Running command  {_truncate(a.get('command', ''))}"),
    "spawn_subagent": ("running", "tool.run", lambda a: f"Spawning sub-agent  {_truncate(a.get('task', a.get('prompt', '')))}"),
    "browser_action": ("running", "tool.run", lambda a: f"Browser  {_truncate((a.get('action', '') or '').upper())}  {_truncate(a.get('url', '') or a.get('selector', '') or a.get('direction', ''))}".rstrip()),
    "write_file": ("editing", "tool.edit", lambda a: f"Creating  {_truncate(a.get('path', a.get('file_path', '')))}"),
    "edit_file": ("editing", "tool.edit", lambda a: f"Editing  {_truncate(a.get('file_path', a.get('path', '')))}"),
    "edit_notebook": ("editing", "tool.edit", lambda a: f"Editing notebook  {_truncate(a.get('path', ''))}"),
    "remember": ("editing", "tool.edit", lambda a: f"Remembering  {_truncate(a.get('fact', ''))}"),
    "architect": ("thinking", "app.muted", lambda a: f"Planning  {_truncate(a.get('task', ''))}"),
    "ask_user": ("thinking", "app.muted", lambda a: f"Asking  {_truncate(a.get('question', ''))}"),
    "think": ("thinking", "app.muted", lambda a: "Thinking\u2026"),
}

# Default presentation for any tool not in the table (incl. MCP tools).
_DEFAULT_ACTIVITY = ("running", "tool.run", lambda name, a: f"{name}")


def format_tool_activity(
    tool_name: str,
    args: Optional[Mapping[str, Any]] = None,
    state: str = "start",
    console: Optional[Console] = None,
    summary: str = "",
) -> Text:
    """Render a single, consistently styled tool-activity line.

    This is the single code path for tool presentation (Req 4.1, 4.2). It maps
    every known tool to a glyph + style + concise label, and falls back to a
    sensible default for unknown/MCP tools.

    ``state`` controls the leading glyph and style:
      - ``"start"``  : the tool's own glyph/style and descriptive label.
      - ``"done"``   : a ``status.ok`` ``✓`` with an optional ``summary``.
      - ``"error"``  : a ``status.err`` ``✗`` with an optional ``summary``.

    Returns a Rich :class:`~rich.text.Text` (themed style names, no raw colors).
    """
    a: Mapping[str, Any] = args or {}
    gmap = glyphs_for(console)

    if state == "done":
        label = _truncate(summary) if summary else "Done"
        return Text.assemble((f"{gmap['done']} ", "status.ok"), (label, "status.ok"))

    if state == "error":
        label = _truncate(summary, 120) if summary else "Error"
        return Text.assemble((f"{gmap['error']} ", "status.err"), (label, "status.err"))

    # state == "start" (default)
    if tool_name in _TOOL_ACTIVITY:
        glyph_key, style, label_fn = _TOOL_ACTIVITY[tool_name]
        label = label_fn(a)
    else:
        glyph_key, style, label_fn = _DEFAULT_ACTIVITY
        label = label_fn(tool_name, a)

    return Text.assemble((f"{gmap[glyph_key]} ", style), (label, style))


# ─────────────────────────────────────────────────────────────────────────────
# Message framing
# ─────────────────────────────────────────────────────────────────────────────
# User and assistant turns are framed with a left gutter bar; tool activity is
# indented under the assistant turn without a bar (Req 4.1, 4.2).
USER_LABEL = "you"
ASSISTANT_LABEL = "omni-dev"


def _gutter_style(role: str) -> str:
    return "assistant.gutter" if role == "assistant" else "user.gutter"


def turn_header(role: str, label: str, console: Optional[Console] = None) -> Text:
    """Return a framed turn header: a gutter bar followed by the role label."""
    bar = glyph("bar", console)
    style = _gutter_style(role)
    return Text.assemble((f"{bar} ", style), (label, style))


def user_turn_header(label: str = USER_LABEL, console: Optional[Console] = None) -> Text:
    """Header line for a user turn (``user.gutter`` left bar)."""
    return turn_header("user", label, console)


def assistant_turn_header(label: str = ASSISTANT_LABEL, console: Optional[Console] = None) -> Text:
    """Header line for an assistant turn (``assistant.gutter`` left bar)."""
    return turn_header("assistant", label, console)


def gutter_line(text: str, role: str = "user", console: Optional[Console] = None) -> Text:
    """Return a single body line prefixed with the role's gutter bar."""
    bar = glyph("bar", console)
    style = _gutter_style(role)
    return Text.assemble((f"{bar} ", style), (text, "default"))


def turn_separator(role: str = "user", console: Optional[Console] = None) -> Text:
    """Return the turn-closing separator (``╰─``) in the role's gutter style."""
    return Text(glyph("turn_end", console), style=_gutter_style(role))


def tool_activity_indent(activity: Text, console: Optional[Console] = None) -> Text:
    """Indent a tool-activity line under the assistant turn (no gutter bar)."""
    indented = Text("  ")
    indented.append_text(activity)
    return indented


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────
_LOGO_LINES = (
    r" /\_/\ ",
    r"( o.o )  omni-dev",
    r" > ^ <  ",
)


def banner(subtitle: str = "agentic coding companion", console: Optional[Console] = None) -> RenderableType:
    """Return the compact logo + title renderable, styled with ``app.banner``.

    Rendered once at startup and on ``/clear``.
    """
    lines: list[Text] = []
    # Title line carries the banner accent; the small logo above it is muted so
    # the title reads as the focal point.
    lines.append(Text(_LOGO_LINES[0], style="app.muted"))
    lines.append(
        Text.assemble(
            ("( o.o )  ", "app.muted"),
            ("omni-dev", "app.banner"),
        )
    )
    lines.append(
        Text.assemble(
            (" > ^ <   ", "app.muted"),
            (subtitle, "app.muted"),
        )
    )
    return Group(*lines)


# ─────────────────────────────────────────────────────────────────────────────
# Status footer
# ─────────────────────────────────────────────────────────────────────────────
def _format_tokens(tokens: Any) -> str:
    try:
        n = int(tokens)
    except (TypeError, ValueError):
        return str(tokens)
    return f"{n:,} tokens"


def _format_cost(cost: Any) -> str:
    try:
        c = float(cost)
    except (TypeError, ValueError):
        return f"~{cost}"
    return f"~${c:.4f}"


def status_footer(
    model: str,
    branch: str,
    tokens: Any = 0,
    cost: Any = 0.0,
    console: Optional[Console] = None,
) -> Text:
    """Return a single styled status line: ``model · branch · tokens · est cost``.

    Token/cost figures come from the cost tracker; the line backs both the
    after-turn footer and the live input bottom toolbar.
    """
    sep = f" {glyph('sep', console)} "
    branch_label = branch or "no-git"
    line = Text(style="app.muted")
    line.append(model or "unknown", style="app.accent")
    line.append(sep, style="app.muted")
    line.append(branch_label, style="app.muted")
    line.append(sep, style="app.muted")
    line.append(_format_tokens(tokens), style="app.muted")
    line.append(sep, style="app.muted")
    line.append(_format_cost(cost), style="app.muted")
    return line
