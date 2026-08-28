#!/usr/bin/env python3
"""Independently verify the Owner-YOLO prediction/training parity artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_OUTPUT = DEFAULT_RESULTS / "qa_receipt.json"


class VerificationError(RuntimeError):
    """Raised when an artifact no longer satisfies the audit contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify(results: Path) -> dict[str, Any]:
    summary = read_json(results / "summary.json")
    receipt = read_json(results / "receipt.json")
    events = pd.read_csv(results / "event_semantic_audit.csv")
    training = pd.read_csv(results / "training_positive_semantic_profile.csv")
    comparisons = read_jsonl(results / "comparison_manifest.jsonl")
    gallery = (results / "comparison_gallery.html").read_text(encoding="utf-8")

    require(summary["experiment_id"] == EXPERIMENT_ID, "summary experiment drift")
    require(receipt["experiment_id"] == EXPERIMENT_ID, "receipt experiment drift")
    require(len(training) == 10_000, "training positive row count drift")
    require(len(events) == 43, "event row count drift")
    require(len(comparisons) == 43, "comparison row count drift")
    require(events["event_order"].nunique() == 43, "event identity duplicate")
    require(sum(events["strict_training_spec_match"].astype(bool)) == 2, "strict-match count drift")
    require(
        set(events.loc[events["strict_training_spec_match"].astype(bool), "event_order"])
        == {27, 30},
        "strict-match identities drift",
    )
    failing = ~events["strict_training_spec_match"].astype(bool)
    require(
        not events.loc[failing, "same_input_strict_alternative_exists"].astype(bool).any(),
        "a failing event unexpectedly has a strict alternative core",
    )
    require(
        (events.loc[failing, "semantic_classification"] == "OUT_OF_TRAINING_SPEC").all(),
        "failing event classification drift",
    )
    require(
        int(training["candle_span_exceeds_ma_span"].astype(bool).sum()) == 9_578,
        "training label vertical-semantics count drift",
    )

    comparison_hashes: set[str] = set()
    for row in comparisons:
        path = (results / str(row["comparison_path"])).resolve()
        require(results.resolve() in path.parents, "comparison path escapes result root")
        require(path.is_file(), f"missing comparison: {path.name}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        require(image is not None, f"comparison decode failed: {path.name}")
        require(image.shape[:2] == (946, 2560), f"comparison dimensions drift: {path.name}")
        actual_sha = sha256_file(path)
        require(actual_sha == row["comparison_sha256"], f"comparison SHA drift: {path.name}")
        require(str(row["comparison_path"]) in gallery, f"gallery link missing: {path.name}")
        comparison_hashes.add(actual_sha)
    require(len(comparison_hashes) == 43, "comparison images are not unique")

    bound_files = {
        "summary_sha256": results / "summary.json",
        "event_csv_sha256": results / "event_semantic_audit.csv",
        "training_csv_sha256": results / "training_positive_semantic_profile.csv",
        "overview_sha256": results / "training_vs_prediction_overview.png",
        "representative_sha256": results / "representative_comparisons.png",
        "gallery_sha256": results / "comparison_gallery.html",
        "comparison_manifest_sha256": results / "comparison_manifest.jsonl",
    }
    for key, path in bound_files.items():
        require(sha256_file(path) == receipt[key], f"receipt hash drift: {path.name}")

    overview = cv2.imread(str(results / "training_vs_prediction_overview.png"), cv2.IMREAD_COLOR)
    representative = cv2.imread(str(results / "representative_comparisons.png"), cv2.IMREAD_COLOR)
    require(overview is not None and overview.shape[:2] == (1598, 2244), "overview image drift")
    require(
        representative is not None and representative.shape[:2] == (1374, 2440),
        "representative image drift",
    )
    require(gallery.count("<article>") == 43, "gallery card count drift")

    return {
        "experiment_id": EXPERIMENT_ID,
        "passed": True,
        "training_positive_rows": len(training),
        "event_rows": len(events),
        "strict_training_spec_matches": 2,
        "out_of_training_spec": 41,
        "failing_events_with_alternative_core": 0,
        "comparison_images": len(comparisons),
        "comparison_image_sha_unique": len(comparison_hashes),
        "gallery_cards": gallery.count("<article>"),
        "receipt_sha256": sha256_file(results / "receipt.json"),
        "safety": {
            "network_read": False,
            "new_inference": False,
            "training": False,
            "label_or_weight_change": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = verify(args.results.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
