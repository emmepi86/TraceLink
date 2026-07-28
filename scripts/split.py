#!/usr/bin/env python3
"""Compatibility wrapper — the implementation lives in `tracelink.splitter`.

Kept so the documented `python3 scripts/split.py ...` invocation keeps working
without installing anything. A tool that only runs after installation is a tool
people cannot try.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from tracelink.splitter import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
