#!/usr/bin/env python3
"""Build the fixed-W20 six-MA box protocol Review50 artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_ma_box_review import DEFAULT_PREREG, build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    receipt = build(args.prereg, args.out)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
