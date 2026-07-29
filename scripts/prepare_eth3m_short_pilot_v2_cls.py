#!/usr/bin/env python3
"""Materialize the audited ETH 3m v2 train/val set as full-frame squares."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from src.detection.eth3m_v2_classification import (
    IMAGE_SIZE,
    OUTPUT_DATASET,
    PREREG,
    SOURCE_DATASET,
    VALIDATION_RECEIPT,
    letterbox_full_frame,
    sha256,
    validate_authorization,
    validate_source,
    verify_prepared,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DATASET)
    parser.add_argument("--output", type=Path, default=OUTPUT_DATASET)
    parser.add_argument("--validation", type=Path, default=VALIDATION_RECEIPT)
    parser.add_argument("--prereg", type=Path, default=PREREG)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if args.verify_only:
        validate_authorization(args.prereg)
        validation, source_meta, _ = validate_source(args.source, args.validation)
        meta = verify_prepared(args.output)
        pinned = {
            "source_manifest_sha256": sha256(args.source / "manifest.csv"),
            "source_build_meta_sha256": sha256(args.source / "build_meta.json"),
            "source_validation_sha256": sha256(args.validation),
            "prereg_sha256": sha256(args.prereg),
        }
        for key, value in pinned.items():
            if meta.get(key) != value:
                raise ValueError(f"prepared {key} no longer matches the launch input")
        if validation["status"] != "passed" or source_meta["diagnostic_pilot_only"] is not True:
            raise ValueError("source validation/diagnostic status changed")
        print(json.dumps({"status": "passed", "counts": meta["counts"]}, ensure_ascii=False))
        return

    prereg = validate_authorization(args.prereg)
    validation, source_meta, rows = validate_source(args.source, args.validation)
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite prepared dataset: {output}")

    prepared_rows: list[dict[str, str | int]] = []
    try:
        for row in rows:
            source_rel = Path(row["image_rel"])
            destination = output / source_rel
            geometry = letterbox_full_frame(args.source / source_rel, destination)
            prepared_rows.append(
                {
                    "sample_id": row["sample_id"],
                    "split": row["split"],
                    "class_name": row["class_name"],
                    "target": row["target"],
                    "anchor_time": row["anchor_time"],
                    "image_rel": source_rel.as_posix(),
                    "source_sha256": row["image_sha256"],
                    "prepared_sha256": sha256(destination),
                    **geometry,
                }
            )
        fieldnames = list(prepared_rows[0])
        with (output / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(prepared_rows)
        counts = source_meta["counts"]
        meta = {
            "dataset": output.name,
            "task": "image_classification_current_tip_short_start",
            "source_dataset": str(args.source.resolve()),
            "source_manifest_sha256": sha256(args.source / "manifest.csv"),
            "source_build_meta_sha256": sha256(args.source / "build_meta.json"),
            "source_validation_receipt": str(args.validation.resolve()),
            "source_validation_sha256": sha256(args.validation),
            "source_validation_status": validation["status"],
            "prereg_sha256": sha256(args.prereg),
            "image_size": IMAGE_SIZE,
            "transform": "deterministic_white_square_letterbox_lanczos",
            "full_frame_preserved": True,
            "right_tip_preserved": True,
            "classes": {"no_start": 0, "short_start": 1},
            "counts": counts,
            "total": len(prepared_rows),
            "included_roots": ["train", "val"],
            "excluded_from_training": ["weak_or_review", "continuous_smoke", "holdout"],
            "owner_authorized_diagnostic_training": prereg["owner_authorization"]["diagnostic_training"],
            "diagnostic_pilot_only": True,
            "formal_gold_dataset": False,
            "promotion_eligible": False,
            "active_eligible": False,
        }
        (output / "build_meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (output / "classes.json").write_text(
            json.dumps(meta["classes"], indent=2) + "\n", encoding="utf-8"
        )
        verified = verify_prepared(output)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    print(json.dumps({"status": "passed", "output": str(output), "counts": verified["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
