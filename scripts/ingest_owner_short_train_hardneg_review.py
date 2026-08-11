#!/usr/bin/env python3
"""Validate and freeze Owner decisions for a train-time active-learning page.

The browser export is joined one-to-one with the frozen review manifest.  The
output remains an audit ledger: even Owner-confirmed train-time negatives stay
training-ineligible until a later dataset builder applies its own deduplication,
bucket, and lineage gates under separately authorized training.
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
DEFAULT_REVIEW_DIR = ROOT / "analysis/output/owner_short_train_hardneg_review200_v1"
DEFAULT_MANIFEST = DEFAULT_REVIEW_DIR / "review_manifest.jsonl"
DEFAULT_BUILD_SUMMARY = DEFAULT_REVIEW_DIR / "summary.json"
ALLOWED_DECISIONS = {"pending", "target", "rebox", "hard_negative"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
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
    if payload.get("source_sha256") != build_summary.get("selected_candidates_sha256"):
        raise ValueError("review source hash does not match frozen selection")
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
    if not all(bool(value) for value in build_summary.get("quality_gates", {}).values()):
        raise ValueError("review build quality gates are not all green")
    return counts


def distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p90": None, "mean": None}
    array = np.asarray(values, dtype=float)
    return {
        "median": float(np.median(array)),
        "p90": float(np.quantile(array, 0.90)),
        "mean": float(np.mean(array)),
    }


def selection_is_causal(rows: list[dict[str, Any]], causal_field: str) -> bool:
    """Validate the builder-specific future-use proof without guessing aliases."""
    missing = [str(row.get("review_id", row.get("event_id", "unknown"))) for row in rows if causal_field not in row]
    if missing:
        raise ValueError(f"causal proof field {causal_field!r} missing for rows: {missing[:5]}")
    return all(not bool(row[causal_field]) for row in rows)


def ingest(
    review_path: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    build_summary_path: Path = DEFAULT_BUILD_SUMMARY,
    output_dir: Path = DEFAULT_REVIEW_DIR,
    affinity_field: str = "hard_negative_affinity",
    causal_field: str = "selection_future_used",
    selection_goal: str = "hard_negative_mining",
    expected_total: int = 200,
) -> dict[str, Any]:
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    manifest = read_jsonl(manifest_path)
    build_summary = json.loads(build_summary_path.read_text(encoding="utf-8"))
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
                "owner_review_protocol": str(payload["protocol"]),
                "hard_negative_candidate": decision == "hard_negative",
                "positive_candidate": decision == "target",
                "geometry_review_required": decision == "rebox",
                "training_eligible": False,
                "training_eligibility_reason": "Owner-confirmed reference only; dataset-builder gates and separate training authorization remain required",
            }
        )
        joined.append(item)
    causal_selection = selection_is_causal(joined, causal_field)

    by_decision: dict[str, Any] = {}
    for decision in ("target", "rebox", "hard_negative"):
        rows = [row for row in joined if row["owner_decision"] == decision]
        by_decision[decision] = {
            "events": len(rows),
            "symbols": len({str(row["symbol"]) for row in rows}),
            "share": len(rows) / len(joined),
            "peak_confidence": distribution([float(row["event_conf_max"]) for row in rows]),
            "selection_affinity": distribution([float(row[affinity_field]) for row in rows]),
        }

    result = {
        "protocol": str(payload["protocol"]),
        "source_sha256": str(payload["source_sha256"]),
        "selection_goal": selection_goal,
        "affinity_field": affinity_field,
        "causal_field": causal_field,
        "review_input_sha256": sha256_file(review_path),
        "rows": len(joined),
        "counts": counts,
        "rates": {
            "target_rate_in_review": counts["target"] / len(joined),
            "hard_negative_rate_in_review": counts["hard_negative"] / len(joined),
        },
        "by_decision": by_decision,
        "by_block": {
            block: dict(Counter(row["owner_decision"] for row in joined if row["candidate_block"] == block))
            for block in sorted({str(row["candidate_block"]) for row in joined})
        },
        "training_gate": {
            "owner_confirmed_train_hard_negative_candidates": counts["hard_negative"],
            "owner_confirmed_target_references": counts["target"],
            "immediately_training_eligible": 0,
            "reason": "freeze as active-learning references; a later dataset builder must preserve time split, deduplicate, bucket-match, and receive separate training authorization",
        },
        "quality_gates": {
            "protocol_matches": payload["protocol"] == build_summary["protocol"],
            "source_hash_matches": payload["source_sha256"] == build_summary["selected_candidates_sha256"],
            "exactly_expected_unique_decisions": len(joined) == expected_total
            and len(decisions) == expected_total,
            "declared_counts_recompute": counts == payload["counts"],
            "no_pending": counts["pending"] == 0,
            "allowed_values_only": set(decisions.values()) <= ALLOWED_DECISIONS,
            "one_to_one_manifest_join": len(joined) == len(manifest),
            "all_rows_train_time": all(not row["holdout_read"] for row in joined),
            "no_owner_box_overlap": all(not row["touches_owner_box_guard"] for row in joined),
            "selection_was_causal": causal_selection,
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
    parser.add_argument("--out", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--affinity-field", default="hard_negative_affinity")
    parser.add_argument("--causal-field", default="selection_future_used")
    parser.add_argument("--selection-goal", default="hard_negative_mining")
    parser.add_argument("--expected-total", type=int, default=200)
    args = parser.parse_args()
    result = ingest(
        args.review_json,
        manifest_path=args.manifest,
        build_summary_path=args.build_summary,
        output_dir=args.out,
        affinity_field=args.affinity_field,
        causal_field=args.causal_field,
        selection_goal=args.selection_goal,
        expected_total=args.expected_total,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
