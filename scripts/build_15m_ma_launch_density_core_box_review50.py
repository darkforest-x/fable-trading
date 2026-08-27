#!/usr/bin/env python3
"""Build the Owner-corrected 15m single density-core box Review50 v3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_density_core_box_review import DEFAULT_PREREG, build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.prereg, args.out), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

