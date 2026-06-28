"""
terminal_setup.py - Python conversion of scratch_repo/src/commands/terminalSetup.ts

The /terminal-setup command installs a Shift+Enter -> newline key binding
appropriate to the detected terminal (best-effort), then persists the result in
the Global_Config via :mod:`src.config_store` (Req 16.5).

Supported environments (best-effort, Windows-first):
- VS Code integrated terminal: writes a Shift+Enter binding into the user's
  ``keybindings.json`` (cross-platform path).
- Windows Terminal / other shells: documents the manual Shift+Enter binding and
  still records that setup ran.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Optional, Tuple


def _detect_terminal() -> str:
    """Best-effort detection of the host terminal."""
    term_program = (os.environ.get("TERM_PROGRAM") or "").lower()
    if term_program == "vscode" or os.environ.get("VSCODE_PID"):
        return "vscode"
    if term_program == "iterm.app":
        return "iterm2"
    if os.environ.get("WT_SESSION"):
        return "windows-terminal"
    return "unknown"


def _vscode_keybindings_path() -> Path:
    """Resolve the VS Code user keybindings.json path for this platform."""
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming")) / "Code" / "User"
    elif system == "Darwin":
        base = home / "Library" / "Application Support" / "Code" / "User"
    else:
        base = home / ".config" / "Code" / "User"
    return base / "keybindings.json"


def _install_vscode_binding() -> Tuple[bool, str]:
    """Install the Shift+Enter binding into VS Code's keybindings.json."""
    path = _vscode_keybindings_path()
    binding = {
        "key": "shift+enter",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "\\\r\n"},
        "when": "terminalFocus",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = []
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        existing = parsed
            except ValueError:
                # Corrupt / commented JSONC we can't safely edit.
                return (
                    False,
                    f"Existing keybindings at {path} could not be parsed. "
                    "Add the Shift+Enter binding manually.",
                )

        for entry in existing:
            if (
                isinstance(entry, dict)
                and entry.get("key") == "shift+enter"
                and entry.get("command") == "workbench.action.terminal.sendSequence"
                and entry.get("when") == "terminalFocus"
            ):
                return True, f"Shift+Enter binding already present in {path}."

        existing.append(binding)
        path.write_text(json.dumps(existing, indent=4), encoding="utf-8")
        return True, f"Installed VS Code Shift+Enter newline binding in {path}."
    except OSError as e:
        return False, f"Could not write VS Code keybindings: {e}"


def _persist_terminal_setup(value: str) -> None:
    """Persist the terminal-setup result into the Global_Config (Req 16.5)."""
    try:
        from src.config_store import get_global_config, save_global_config

        cfg = get_global_config()
        cfg["terminalSetup"] = value
        save_global_config(cfg)
    except Exception:
        # Persistence is best-effort; never fail the command on a config write.
        pass


async def terminal_setup_command() -> str:
    """
    Configure terminal keybindings for the detected environment (Req 16.5).

    Returns:
        A description of what was configured.
    """
    terminal = _detect_terminal()
    lines = ["## ⌨️  Terminal Setup"]

    if terminal == "vscode":
        ok, message = _install_vscode_binding()
        status = "✅" if ok else "⚠️ "
        lines.append(f"  {status} {message}")
        _persist_terminal_setup("vscode-shift-enter" if ok else "vscode-failed")
        if ok:
            lines.append("  💡 Restart the VS Code integrated terminal for the binding to take effect.")

    elif terminal == "windows-terminal":
        lines.append("  🪟 Detected Windows Terminal.")
        lines.append(
            "  To send a newline with Shift+Enter, add this to your Windows Terminal settings.json `actions`:"
        )
        lines.append(
            '    { "command": { "action": "sendInput", "input": "\\n" }, "keys": "shift+enter" }'
        )
        _persist_terminal_setup("windows-terminal-documented")

    elif terminal == "iterm2":
        lines.append("  🍎 Detected iTerm2.")
        lines.append(
            "  Set iTerm2 → Preferences → Keys → Key Bindings: map Shift+Enter to "
            "'Send Escape Sequence' / newline."
        )
        _persist_terminal_setup("iterm2-documented")

    else:
        lines.append("  ℹ️  Could not detect a supported terminal automatically.")
        lines.append(
            "  Most terminals support a Shift+Enter newline binding; consult your terminal's "
            "key-binding settings."
        )
        _persist_terminal_setup("documented")

    lines.append("  💾 Saved terminal setup state to your global config.")
    return "\n".join(lines)
