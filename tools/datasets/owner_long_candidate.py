#!/usr/bin/env python3
"""CLI wrapper for the review-gated Owner-long candidate ledger."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from yoyo.datasets.owner_long_candidate import main


if __name__ == "__main__":
    raise SystemExit(main())
