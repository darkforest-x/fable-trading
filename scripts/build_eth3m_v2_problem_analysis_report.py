#!/usr/bin/env python3
"""Generate the canonical artifact for the ETH 3m v2 failure report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.reporting.eth3m_v2_problem_analysis import (
    OUTPUT_DIR,
    PREREG_PATH,
    QUALITY_AUDIT_PATH,
    SUMMARY_PATH,
    build_from_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--prereg", type=Path, default=PREREG_PATH)
    parser.add_argument("--quality-audit", type=Path, default=QUALITY_AUDIT_PATH)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    artifact = build_from_files(args.summary, args.prereg, args.quality_audit)
    args.out.mkdir(parents=True, exist_ok=True)
    artifact_path = args.out / "artifact.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"artifact": str(artifact_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
