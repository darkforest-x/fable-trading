#!/usr/bin/env python3
"""Thin CLI for independent ETH 3m v2 dataset validation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.detection.eth3m_v2_validation import DEFAULT_DATASET, DEFAULT_OUT, validate_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    receipt = validate_dataset(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
