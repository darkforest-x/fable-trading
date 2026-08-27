#!/usr/bin/env python3
"""Validate a complete Owner Review50 export without unlocking training."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_ma_box_review import (
    DEFAULT_PREREG,
    ROOT,
    read_json,
    read_jsonl,
    sha256_file,
    validate_owner_review_payload,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("answers", type=Path, help="JSON exported by the Review50 HTML")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument(
        "--review-manifest",
        type=Path,
        default=DEFAULT_PREREG.parent / "results" / "review_manifest.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_PREREG.parent / "results" / "owner_review_summary.json",
    )
    args = parser.parse_args()
    paths = (args.answers.resolve(), args.prereg.resolve(), args.review_manifest.resolve())
    if any(not path.is_file() for path in paths):
        missing = [str(path) for path in paths if not path.is_file()]
        raise FileNotFoundError(f"missing frozen review input: {missing}")
    output = args.out.resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("owner review summary must stay inside the repository") from exc
    if output.exists():
        raise FileExistsError(f"refusing to overwrite owner review summary: {output}")
    summary = validate_owner_review_payload(
        read_json(paths[0]),
        read_jsonl(paths[2]),
        prereg_sha256=sha256_file(paths[1]),
        review_manifest_sha256=sha256_file(paths[2]),
    )
    summary["answers_path"] = str(paths[0].relative_to(ROOT)) if paths[0].is_relative_to(ROOT) else str(paths[0])
    summary["answers_sha256"] = sha256_file(paths[0])
    write_json(output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
