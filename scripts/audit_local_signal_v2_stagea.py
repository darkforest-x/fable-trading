#!/usr/bin/env python3
"""P0 audit for owner-authorized Local-Signal V2 Stage-A random crops.

Stage A is intentionally non-causal relative to the historical decision bar:
every positive image must contain at least one real market bar after decision
so the label can occupy different positions in the real candle sequence.  The
entire window must nevertheless remain before the project holdout.  Passing
this audit means "eligible for offline pretraining" only, never production.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.audit_local_signal_v2 import refine_split_audit
from scripts.audit_w20_midbox_causality import (
    HOLDOUT_START,
    audit_causality,
    audit_conservation,
    audit_holdout,
    quantiles,
)

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT / "datasets" / "local_signal_v2_stagea_randomcrop_v1"


def audit_real_candle_position(pos_rows: list[dict], summary: dict) -> dict:
    """Verify real-candle anchor positions against the frozen four-bucket target."""
    declarations = summary.get("position_buckets") or []
    declared = {str(item["name"]): item for item in declarations}
    expected_names = set(declared)
    counts = Counter(str(row.get("position_bucket")) for row in pos_rows)
    n = max(len(pos_rows), 1)
    shares = {name: counts[name] / n for name in sorted(expected_names)}
    target_shares = {
        name: float(declared[name]["target_share"]) for name in sorted(expected_names)
    }
    deviations = {
        name: abs(shares[name] - target_shares[name]) for name in sorted(expected_names)
    }
    tolerance = float(summary.get("position_share_tolerance", 0.0))
    bad_bucket_rows = 0
    for row in pos_rows:
        name = str(row.get("position_bucket"))
        ratio = float(row.get("anchor_x_ratio", float("nan")))
        item = declared.get(name)
        if item is None:
            bad_bucket_rows += 1
            continue
        lo, hi = float(item["lo"]), float(item["hi"])
        inside = lo <= ratio < hi
        if name == "right":
            inside = lo <= ratio <= hi
        if not inside:
            bad_bucket_rows += 1
    future = [int(row.get("future_bars", 0)) for row in pos_rows]
    real_position_support = [float(row["anchor_x_ratio"]) for row in pos_rows]
    no_blank_layout = all(
        int(row.get("right_blank_slots", 0)) == 0 for row in pos_rows
    )
    all_buckets_present = bool(expected_names and all(counts[name] > 0 for name in expected_names))
    max_deviation = max(deviations.values(), default=1.0)
    result = {
        "coordinate": "anchor_offset / (window_len - 1) over real candles",
        "counts": dict(sorted(counts.items())),
        "shares": shares,
        "target_shares": target_shares,
        "absolute_share_deviation": deviations,
        "maximum_absolute_share_deviation": max_deviation,
        "share_tolerance": tolerance,
        "all_buckets_present": all_buckets_present,
        "n_rows_outside_declared_bucket": bad_bucket_rows,
        "anchor_x_ratio": quantiles(real_position_support),
        "real_bars_after_decision": quantiles([float(value) for value in future]),
        "all_boxes_have_real_bars_to_right": bool(future and min(future) >= 1),
        "right_blank_slots_all_zero": no_blank_layout,
    }
    result["pass"] = bool(
        pos_rows
        and all_buckets_present
        and bad_bucket_rows == 0
        and max_deviation <= tolerance
        and result["all_boxes_have_real_bars_to_right"]
        and no_blank_layout
    )
    return result


def run_audit(dataset: Path) -> dict:
    pos_rows = json.loads((dataset / "w20_manifest.json").read_text())
    neg_rows = json.loads((dataset / "w20_neg_manifest.json").read_text())
    summary = json.loads((dataset / "stagea_summary.json").read_text())
    causality = audit_causality(pos_rows)
    split = refine_split_audit(pos_rows, neg_rows, summary)
    holdout = audit_holdout(pos_rows)
    negative_end = pd.to_datetime(
        [row.get("end_time") for row in neg_rows], utc=True, errors="coerce"
    )
    holdout["n_negative_in_holdout"] = int((negative_end >= HOLDOUT_START).sum())
    holdout["clean"] = bool(
        holdout["clean"] and holdout["n_negative_in_holdout"] == 0
    )
    conservation = audit_conservation(dataset, pos_rows, neg_rows)
    position = audit_real_candle_position(pos_rows, summary)
    rows = pos_rows + neg_rows
    traceability = {
        "n_samples": len(rows),
        "all_samples_traceable_to_market_bar": bool(
            rows
            and all(
                row.get("symbol")
                and row.get("win_start") is not None
                and row.get("win_len") is not None
                and row.get("end_time") is not None
                for row in rows
            )
        ),
    }
    semantic_flags = {
        "all_rows_stage_a": bool(rows and all(row.get("stage") == "A" for row in rows)),
        "all_rows_production_ineligible": bool(
            rows and all(row.get("production_eligible") is False for row in rows)
        ),
        "summary_production_ineligible": summary.get("production_eligible") is False,
    }
    gates = {
        "stage_a_has_real_post_decision_bars": bool(
            causality["verdict"] == "stage_a_only"
            and causality["n_future_gt0"] == causality["n_positive"]
        ),
        "box_end_le_decision": bool(causality["box_end_le_decision"]),
        "real_candle_position_diversity": bool(position["pass"]),
        "no_event_crosses_split": split["n_events_crossing_split"] == 0,
        "time_based_split": bool(split["is_time_split"])
        and bool(split["negative_time_split"]["pass"]),
        "no_holdout_in_training": bool(holdout["clean"]),
        "labels_in_bounds": conservation["n_labels_out_of_bounds"] == 0,
        "manifest_conserved": bool(conservation["conserved"]),
        "market_bar_traceability": bool(
            traceability["all_samples_traceable_to_market_bar"]
        ),
        "stage_a_never_production_eligible": all(semantic_flags.values()),
    }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "summary_protocol": summary.get("protocol"),
        "causality": causality,
        "real_candle_position": position,
        "split": split,
        "holdout": holdout,
        "conservation": conservation,
        "traceability": traceability,
        "semantic_flags": semantic_flags,
        "gates": gates,
        "p0_pass": all(gates.values()),
        "training_eligible_stage_a": all(gates.values()),
        "production_eligible": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis" / "output" / "p0_local_signal_v2_stagea_audit.json",
    )
    args = parser.parse_args()
    result = run_audit(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))
    print(f"p0_pass={result['p0_pass']} production_eligible={result['production_eligible']}")
    return 0 if result["p0_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
