#!/usr/bin/env python3
"""Thin entrypoint shim so `python3 terrakettle.py ...` works from a checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from terrakettle.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
