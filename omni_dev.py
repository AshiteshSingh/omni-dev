#!/usr/bin/env python3
import sys
import asyncio
from src.cli.interface import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
