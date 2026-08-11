#!/usr/bin/env python3
"""Validate and freeze Owner decisions for the 331-event canary review.

The input is a browser-exported JSON payload. It is joined one-to-one with the
frozen review manifest using review_id. Outputs remain non-training artifacts:
hard negatives are only candidates until a later dataset builder performs time
split, overlap, and diversity checks under a separately authorized experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_DIR = (
    ROOT / "analysis/output/owner_short_gold_center_hardneg_canary_review331_v3"
)
DEFAULT_MANIFEST = DEFAULT_REVIEW_DIR / "review_manifest.jsonl"
DEFAULT_BUILD_SUMMARY = DEFAULT_REVIEW_DIR / "summary.json"
DEFAULT_POSITIVE_MANIFEST = (
    ROOT / "datasets/owner_short_gold_center_v1/positive_manifest.jsonl"
)
ALLOWED_DECISIONS = {"target", "rebox", "hard_negative"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def recompute_counts(decisions: dict[str, str]) -> dict[str, int]:
    counts = Counter(decisions.values())
    return {
        "pending": int(counts.get("pending", 0)),
        "target": int(counts.get("target", 0)),
        "rebox": int(counts.get("rebox", 0)),
        "hard_negative": int(counts.get("hard_negative", 0)),
    }


def validate_review(
    payload: dict[str, Any],
    manifest: list[dict[str, Any]],
    build_summary: dict[str, Any],
) -> dict[str, int]:
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        raise ValueError("decisions must be an object")
    review_ids = [str(row["review_id"]) for row in manifest]
    if len(review_ids) != len(set(review_ids)):
        raise ValueError("review manifest has duplicate review_id")
    if len({str(row["event_id"]) for row in manifest}) != len(manifest):
        raise ValueError("review manifest has duplicate event_id")
    if payload.get("protocol") != build_summary.get("protocol"):
        raise ValueError("review protocol does not match frozen build")
    if payload.get("source_sha256") != build_summary.get("events_source_sha256"):
        raise ValueError("review source hash does not match frozen events")
    if int(payload.get("total", -1)) != len(manifest):
        raise ValueError("review total does not match manifest")
    if set(decisions) != set(review_ids):
        missing = sorted(set(review_ids) - set(decisions))
        extra = sorted(set(decisions) - set(review_ids))
        raise ValueError(f"decision IDs mismatch: missing={missing[:5]} extra={extra[:5]}")
    invalid = sorted(set(decisions.values()) - ALLOWED_DECISIONS)
    if invalid:
        raise ValueError(f"invalid decisions: {invalid}")
    counts = recompute_counts({str(key): str(value) for key, value in decisions.items()})
    declared = {key: int(payload.get("counts", {}).get(key, -1)) for key in counts}
    if counts != declared:
        raise ValueError(f"declared counts mismatch: declared={declared} actual={counts}")
    if counts["pending"] != 0:
        raise ValueError("review is incomplete")
    if int(build_summary.get("owner_decisions_preselected", -1)) != 0:
        raise ValueError("review build was not neutral")
    if int(build_summary.get("training_eligible", -1)) != 0:
        raise ValueError("review build already contains training-eligible rows")
    return counts


def distribution(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {
        "median": float(np.median(array)),
        "p90": float(np.quantile(array, 0.90)),
        "mean": float(np.mean(array)),
    }


def label_metrics(rows: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    selected = [row for row in rows if row["owner_decision"] == decision]
    return {
        "events": len(selected),
        "symbols": len({str(row["symbol"]) for row in selected}),
        "share": len(selected) / len(rows),
        "first_confidence": distribution([float(row["conf"]) for row in selected]),
        "peak_confidence": distribution([float(row["event_conf_max"]) for row in selected]),
    }


def threshold_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    selected = [row for row in rows if float(row["event_conf_max"]) >= threshold]
    counts = Counter(row["owner_decision"] for row in selected)
    semantic_positive = counts["target"] + counts["rebox"]
    total_target = sum(row["owner_decision"] == "target" for row in rows)
    total_semantic_positive = sum(
        row["owner_decision"] in {"target", "rebox"} for row in rows
    )
    return {
        "peak_confidence_gte": threshold,
        "events": len(selected),
        "target": int(counts["target"]),
        "rebox": int(counts["rebox"]),
        "hard_negative": int(counts["hard_negative"]),
        "exact_precision": counts["target"] / len(selected) if selected else None,
        "semantic_precision": semantic_positive / len(selected) if selected else None,
        "exact_recall_within_reviewed_events": counts["target"] / total_target,
        "semantic_recall_within_reviewed_events": semantic_positive / total_semantic_positive,
    }


def rank_bucket_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundaries = [(1, 50), (51, 100), (101, 200), (201, len(rows))]
    result = []
    for start, end in boundaries:
        selected = rows[start - 1 : end]
        counts = Counter(row["owner_decision"] for row in selected)
        semantic_positive = counts["target"] + counts["rebox"]
        result.append(
            {
                "rank_start": start,
                "rank_end": end,
                "events": len(selected),
                "target": int(counts["target"]),
                "rebox": int(counts["rebox"]),
                "hard_negative": int(counts["hard_negative"]),
                "exact_precision": counts["target"] / len(selected),
                "semantic_precision": semantic_positive / len(selected),
            }
        )
    return result


def ingest(
    review_path: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    build_summary_path: Path = DEFAULT_BUILD_SUMMARY,
    positive_manifest_path: Path = DEFAULT_POSITIVE_MANIFEST,
    output_dir: Path = DEFAULT_REVIEW_DIR,
) -> dict[str, Any]:
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    manifest = read_jsonl(manifest_path)
    build_summary = json.loads(build_summary_path.read_text(encoding="utf-8"))
    positives = read_jsonl(positive_manifest_path)
    counts = validate_review(payload, manifest, build_summary)
    decisions = {str(key): str(value) for key, value in payload["decisions"].items()}
    joined: list[dict[str, Any]] = []
    for row in manifest:
        decision = decisions[str(row["review_id"])]
        item = dict(row)
        item.update(
            {
                "owner_decision": decision,
                "owner_confirmed": True,
                "owner_review_protocol": payload["protocol"],
                "hard_negative_candidate": decision == "hard_negative",
                "positive_candidate": decision == "target",
                "geometry_review_required": decision == "rebox",
                "training_eligible": False,
                "training_eligibility_reason": "post-val canary reference; direct training would violate the frozen time split",
            }
        )
        joined.append(item)

    total = len(joined)
    semantic_positive = counts["target"] + counts["rebox"]
    train_end_max = max(
        str(row["end_time"]) for row in positives if row["split"] == "train"
    )
    val_end_max = max(
        str(row["end_time"]) for row in positives if row["split"] == "val"
    )
    review_start = min(str(row["decision_time"]) for row in joined)
    review_end = max(str(row["decision_time"]) for row in joined)
    review_is_postval = review_start > val_end_max
    result = {
        "protocol": payload["protocol"],
        "source_sha256": payload["source_sha256"],
        "review_input_sha256": sha256_file(review_path),
        "rows": total,
        "counts": counts,
        "rates": {
            "exact_target_precision": counts["target"] / total,
            "semantic_positive_precision": semantic_positive / total,
            "hard_negative_rate": counts["hard_negative"] / total,
            "rebox_rate_all": counts["rebox"] / total,
            "rebox_rate_within_semantic_positive": counts["rebox"] / semantic_positive,
        },
        "by_decision": {
            decision: label_metrics(joined, decision)
            for decision in ("target", "rebox", "hard_negative")
        },
        "by_peak_confidence_threshold": [
            threshold_metrics(joined, threshold)
            for threshold in (0.50, 0.60, 0.70, 0.80, 0.90)
        ],
        "by_peak_confidence_rank": rank_bucket_metrics(joined),
        "chronology": {
            "frozen_train_end_max": train_end_max,
            "frozen_val_end_max": val_end_max,
            "review_decision_start": review_start,
            "review_decision_end": review_end,
            "review_is_strictly_postval": review_is_postval,
        },
        "training_gate": {
            "owner_confirmed_hard_negative_references": counts["hard_negative"],
            "immediately_training_eligible": 0,
            "current_time_split_training_eligible": 0,
            "reason": "all reviewed events are post-val canary rows; mine analogous negatives inside the frozen train block",
            "target_rows_reserved_from_negative_training": counts["target"],
            "rebox_rows_reserved_pending_geometry": counts["rebox"],
        },
        "quality_gates": {
            "protocol_matches": payload["protocol"] == build_summary["protocol"],
            "source_hash_matches": payload["source_sha256"] == build_summary["events_source_sha256"],
            "exactly_331_unique_decisions": total == 331 and len(decisions) == 331,
            "declared_counts_recompute": counts == payload["counts"],
            "no_pending": counts["pending"] == 0,
            "allowed_values_only": set(decisions.values()) <= ALLOWED_DECISIONS,
            "one_to_one_manifest_join": len(joined) == len(manifest),
            "review_is_postval_canary": review_is_postval,
            "nothing_auto_training_eligible": all(not row["training_eligible"] for row in joined),
        },
    }
    if not all(result["quality_gates"].values()):
        raise RuntimeError(result["quality_gates"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "owner_review_decisions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_jsonl(output_dir / "owner_review_labeled_manifest.jsonl", joined)
    (output_dir / "owner_review_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--build-summary", type=Path, default=DEFAULT_BUILD_SUMMARY)
    parser.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_REVIEW_DIR)
    args = parser.parse_args()
    result = ingest(
        args.review_json,
        args.manifest,
        args.build_summary,
        args.positive_manifest,
        args.out,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
