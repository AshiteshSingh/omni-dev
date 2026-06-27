#!/usr/bin/env python3
"""
Omni-Dev entry point.
Sets UTF-8 encoding before anything else to support emoji on Windows.
"""
import sys
import os

# ── Force UTF-8 output on Windows (fixes emoji rendering in cmd/PowerShell) ──
if sys.platform == "win32":
    import io
    # Reconfigure stdout/stderr to UTF-8 with replacement for unsupported chars
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

import asyncio
from src.cli.interface import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
