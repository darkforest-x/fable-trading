#!/usr/bin/env python3
"""Materialize the owner-confirmed causal Stage-B curriculum dataset.

The source is the mechanically valid causal-blank W30 dataset whose pixels were
previously rejected only as a substitute for Stage A. Owner 2026-08-11 later
confirmed that the same geometry is correct for Stage B: real market bars end
at decision, and 0--12 right-side slots are layout-only. This script creates a
new version without mutating or reclassifying the historical rejected folder.

Every image and label is copied byte-for-byte, rehashed, and given an explicit
``production_eligible=false`` curriculum role. Source manifests are pinned by
SHA-256 so an unrelated rebuild cannot silently enter the new version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT / "datasets/local_signal_v2_p1_causal_blank_w30_v3"
DEFAULT_OUT = PROJECT / "datasets/local_signal_v2_stageb_from_stagea_v1"
DEFAULT_AUDIT = PROJECT / "analysis/output/p0_local_signal_v2_p1_causal_blank_w30_v3_audit.json"
PROTOCOL = "local_signal_v2_stageb_from_stagea_v1_20260811"
SOURCE_POS_SHA256 = "f82a49100949b7b10425cd6c822830083fc758f77a94db275cfd2213d6fb43a1"
SOURCE_NEG_SHA256 = "8357528442c9e0c8e1d63a2fb2b4497f1cb96d51462bea7b4c5a0af008a594c8"
STAGE_A_WEIGHTS_SHA256 = "c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source(source: Path, audit_path: Path) -> tuple[list[dict], list[dict], dict]:
    pos_manifest = source / "w20_manifest.json"
    neg_manifest = source / "w20_neg_manifest.json"
    if sha256_file(pos_manifest) != SOURCE_POS_SHA256:
        raise ValueError("source positive manifest SHA-256 changed")
    if sha256_file(neg_manifest) != SOURCE_NEG_SHA256:
        raise ValueError("source negative manifest SHA-256 changed")
    audit = json.loads(audit_path.read_text())
    if not audit.get("p0_pass") or not audit.get("mechanical_canvas_gates_pass"):
        raise ValueError("source mechanical P0 did not pass")
    if not audit.get("holdout", {}).get("clean"):
        raise ValueError("source audit is not holdout-clean")
    if audit.get("causality", {}).get("n_future_gt0") != 0:
        raise ValueError("source is not strictly causal")
    positives = json.loads(pos_manifest.read_text())
    negatives = json.loads(neg_manifest.read_text())
    summary = json.loads((source / "stageb_summary.json").read_text())
    if len(positives) != 2388 or len(negatives) != 2388:
        raise ValueError("source row count changed")
    return positives, negatives, summary


def rewrite_and_copy_row(row: dict, source: Path, out: Path, *, sample_type: str) -> dict:
    source_image = PROJECT / row["out_img"]
    source_label = PROJECT / row["out_lbl"]
    image_relative = source_image.relative_to(source)
    label_relative = source_label.relative_to(source)
    out_image = out / image_relative
    out_label = out / label_relative
    out_image.parent.mkdir(parents=True, exist_ok=True)
    out_label.parent.mkdir(parents=True, exist_ok=True)
    actual_source_image_sha = sha256_file(source_image)
    if actual_source_image_sha != row["image_sha256"]:
        raise ValueError(f"source image hash mismatch: {source_image}")
    source_label_sha = sha256_file(source_label)
    shutil.copy2(source_image, out_image)
    shutil.copy2(source_label, out_label)
    if sha256_file(out_image) != actual_source_image_sha:
        raise ValueError(f"copied image hash mismatch: {out_image}")
    if sha256_file(out_label) != source_label_sha:
        raise ValueError(f"copied label hash mismatch: {out_label}")
    return {
        **row,
        "out_img": str(out_image.relative_to(PROJECT)),
        "out_lbl": str(out_label.relative_to(PROJECT)),
        "label_sha256": source_label_sha,
        "sample_type": sample_type,
        "dataset_protocol": PROTOCOL,
        "curriculum_role": "stage_b_causal_finetune_from_stage_a",
        "production_eligible": False,
        "owner_confirmed_stage_b_layout": True,
    }


def materialize(source: Path, out: Path, audit_path: Path) -> dict:
    positives, negatives, source_summary = validate_source(source, audit_path)
    copied_positive = []
    copied_negative = []
    for index, row in enumerate(positives, start=1):
        copied_positive.append(rewrite_and_copy_row(row, source, out, sample_type="positive"))
        if index % 500 == 0:
            print(f"positive {index}/{len(positives)}", flush=True)
    for index, row in enumerate(negatives, start=1):
        copied_negative.append(
            rewrite_and_copy_row(row, source, out, sample_type="easy_negative")
        )
        if index % 500 == 0:
            print(f"negative {index}/{len(negatives)}", flush=True)
    all_rows = copied_positive + copied_negative
    counts = Counter((row["split"], row["sample_type"]) for row in all_rows)
    summary = {
        **source_summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "out": str(out.relative_to(PROJECT)),
        "source_dataset": str(source.relative_to(PROJECT)),
        "source_positive_manifest_sha256": SOURCE_POS_SHA256,
        "source_negative_manifest_sha256": SOURCE_NEG_SHA256,
        "source_mechanical_audit": str(audit_path.relative_to(PROJECT)),
        "owner_confirmed_stage_b_layout_at": "2026-08-11",
        "historical_stage_a_rejection_preserved": True,
        "curriculum_initialization_weights_sha256": STAGE_A_WEIGHTS_SHA256,
        "training_role": "stage_b_causal_finetune_from_stage_a",
        "training_eligible_stage_b": True,
        "production_eligible": False,
        "holdout_read": False,
        "counts": {
            "train_positive": counts[("train", "positive")],
            "val_positive": counts[("val", "positive")],
            "train_negative": counts[("train", "easy_negative")],
            "val_negative": counts[("val", "easy_negative")],
        },
        "pixels_copied_byte_for_byte": True,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "w20_manifest.json").write_text(json.dumps(copied_positive, indent=2) + "\n")
    (out / "w20_neg_manifest.json").write_text(json.dumps(copied_negative, indent=2) + "\n")
    (out / "stageb_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "w20_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_start\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    materialize(args.source.resolve(), args.out.resolve(), args.audit.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
