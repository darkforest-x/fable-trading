#!/usr/bin/env python3
"""Build the fully boxed strict 15m MA-launch example pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_owner_autofill_review import DEFAULT_PREREG, build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.prereg, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
