#!/usr/bin/env python3
"""
Omni-Dev entry point.

Enforces UTF-8 terminal I/O (so emoji/glyphs render on Windows) before anything
else, then hands off to the interactive interface. UTF-8 handling is delegated
to the centralized ``src.cli.theme.enforce_utf8`` helper so there is a single
source of truth (no more inline chcp/stdout-wrapping here).
"""
import sys

# ── Centralized Windows UTF-8 enforcement (must run before the Console is built) ──
from src.cli import theme as _theme
_theme.enforce_utf8()

import asyncio
from src.cli.interface import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
