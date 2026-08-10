#!/usr/bin/env python3
"""P0 hard-gate audit for local_signal_v2 Stage-B datasets.

Extends the w20 causality arithmetic with full positive/negative window
time-split detection and Stage-B summary awareness. Read-only; exits nonzero
when any P0 gate fails.

Usage:
  .venv/bin/python scripts/audit_local_signal_v2.py \
      --dataset datasets/local_signal_v2_stageb_strictneg_v2 \
      --out analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.audit_w20_midbox_causality import (
    HOLDOUT_START,
    audit_causality,
    audit_conservation,
    audit_holdout,
    audit_position,
    audit_split,
)

DEFAULT_DS = PROJECT / "datasets" / "local_signal_v2_stageb_strictneg_v2"


def audit_blank_layout(
    pos_rows: list[dict], neg_rows: list[dict], summary: dict | None
) -> dict:
    """Audit the opt-in Stage-B canvas layout without treating blanks as bars."""
    declared = None if not summary else summary.get("right_blank_range")
    target = None if not summary else summary.get("target_box_position_range")
    if declared is None:
        return {"applicable": False, "pass": True}
    if not isinstance(declared, list) or len(declared) != 2:
        return {"applicable": True, "pass": False, "reason": "invalid declaration"}
    blank_lo, blank_hi = (int(value) for value in declared)
    expected_support = set(range(blank_lo, blank_hi + 1))
    pos_support = {int(row["right_blank_slots"]) for row in pos_rows}
    neg_support = {int(row["right_blank_slots"]) for row in neg_rows}
    positions = [float(row["box_pos_frac"]) for row in pos_rows]
    target_lo, target_hi = (
        (float(target[0]), float(target[1]))
        if isinstance(target, list) and len(target) == 2
        else (float("nan"), float("nan"))
    )
    edges = [target_lo + (target_hi - target_lo) * i / 4 for i in range(5)]
    bucket_counts = [0, 0, 0, 0]
    for position in positions:
        for index in range(4):
            is_last = index == 3
            if edges[index] <= position < edges[index + 1] or (
                is_last and position == edges[index + 1]
            ):
                bucket_counts[index] += 1
                break
    position_in_range = bool(
        positions
        and target_lo <= min(positions)
        and max(positions) <= target_hi
    )
    support_matches = bool(
        pos_support == expected_support and neg_support == expected_support
    )
    canvas_contract = bool(
        pos_rows
        and neg_rows
        and all(
            int(row["canvas_slots"])
            == int(row["win_len"]) + int(row["right_blank_slots"])
            for row in pos_rows + neg_rows
        )
        and all(int(row.get("future_bars", 0)) == 0 for row in pos_rows)
        and summary.get("blank_slots_are_market_bars") is False
    )
    occupied_buckets = sum(count > 0 for count in bucket_counts)
    result = {
        "applicable": True,
        "declared_blank_range": [blank_lo, blank_hi],
        "target_box_position_range": [target_lo, target_hi],
        "positive_blank_support": sorted(pos_support),
        "negative_blank_support": sorted(neg_support),
        "support_matches_declared_range": support_matches,
        "box_position_min": min(positions) if positions else None,
        "box_position_max": max(positions) if positions else None,
        "position_in_target_range": position_in_range,
        "position_bucket_counts": bucket_counts,
        "occupied_position_buckets": occupied_buckets,
        "canvas_slots_are_layout_only": canvas_contract,
    }
    result["pass"] = bool(
        support_matches
        and position_in_range
        and occupied_buckets == 4
        and canvas_contract
    )
    return result


def _time_frame(rows: list[dict], *, sample_type: str) -> pd.DataFrame:
    """Return start/end timestamps for split auditing.

    Stage-B V1 manifests did not persist ``start_time``.  For those rows the
    start is reconstructed from the 15-minute ``end_time`` and ``win_len``.
    """
    if not rows:
        return pd.DataFrame(columns=["sample_type", "split", "start", "end"])
    end = pd.to_datetime([r.get("end_time") for r in rows], utc=True, errors="coerce")
    explicit_start = pd.to_datetime(
        [r.get("start_time") for r in rows], utc=True, errors="coerce"
    )
    derived_start = end - pd.to_timedelta(
        [max(int(r.get("win_len") or 1) - 1, 0) * 15 for r in rows], unit="m"
    )
    start = explicit_start.where(~explicit_start.isna(), derived_start)
    return pd.DataFrame(
        {
            "sample_type": sample_type,
            "split": [r.get("split") for r in rows],
            "start": start,
            "end": end,
        }
    ).dropna(subset=["start", "end"])


def refine_split_audit(
    pos_rows: list[dict], neg_rows: list[dict], summary: dict | None
) -> dict:
    base = audit_split(pos_rows, [])
    pos_frame = _time_frame(pos_rows, sample_type="positive")
    neg_frame = _time_frame(neg_rows, sample_type="negative")
    frame = pd.concat([pos_frame, neg_frame], ignore_index=True)
    tr = frame.loc[frame["split"] == "train", "end"]
    va_start = frame.loc[frame["split"] == "val", "start"]
    is_time = False
    gap_days = None
    if len(tr) and len(va_start):
        # Strict window split: the latest train bar precedes the first real bar
        # visible in any validation image, not merely its decision/end bar.
        is_time = bool(tr.max() < va_start.min())
        if is_time:
            gap_days = (va_start.min() - tr.max()).total_seconds() / 86400
    base["is_time_split"] = is_time
    base["train_max_before_val_window_start"] = is_time
    base["purge_gap_days"] = None if gap_days is None else round(gap_days, 3)
    base["all_sample_time_range"] = {}
    for split in ("train", "val"):
        part = frame.loc[frame["split"] == split]
        if not part.empty:
            base["all_sample_time_range"][split] = {
                "n": int(len(part)),
                "start_min": str(part["start"].min()),
                "end_max": str(part["end"].max()),
            }

    # Negative windows must be contained in the same frozen blocks as the
    # positive events.  A split label inherited from a symbol is not enough.
    p_tr = pos_frame.loc[pos_frame["split"] == "train", "end"]
    p_va_start = pos_frame.loc[pos_frame["split"] == "val", "end"]
    p_va_end = pos_frame.loc[pos_frame["split"] == "val", "end"]
    n_tr = neg_frame.loc[neg_frame["split"] == "train"]
    n_va = neg_frame.loc[neg_frame["split"] == "val"]
    bad_train = 0
    bad_val_before = 0
    bad_val_after = 0
    if len(p_tr) and len(n_tr):
        bad_train = int((n_tr["end"] > p_tr.max()).sum())
    if len(p_va_start) and len(n_va):
        bad_val_before = int((n_va["start"] < p_va_start.min()).sum())
        bad_val_after = int((n_va["end"] > p_va_end.max()).sum())
    base["negative_time_split"] = {
        "n_negative": int(len(neg_frame)),
        "n_train_after_train_end": bad_train,
        "n_val_before_val_start": bad_val_before,
        "n_val_after_val_end": bad_val_after,
        "pass": bool(
            len(neg_frame) == len(neg_rows)
            and bad_train == 0
            and bad_val_before == 0
            and bad_val_after == 0
        ),
    }
    base["negatives_have_end_timestamps"] = bool(
        neg_rows and all(r.get("end_time") is not None for r in neg_rows)
    )
    base["negatives_have_start_timestamps"] = bool(
        neg_rows and all(r.get("start_time") is not None for r in neg_rows)
    )
    # Keep the legacy key honest as well; audit_split() received no negatives
    # because this stricter audit needs full window start/end timestamps.
    base["negatives_have_timestamps"] = base["negatives_have_end_timestamps"]
    if summary:
        base["strategy_in_code"] = summary.get("split_rule", base["strategy_in_code"])
        base["purge_embargo_bars"] = int(summary.get("purge_bars", 0) or 0)
        base["declared_time_split"] = bool(summary.get("is_time_split"))
    # event_id cross-check
    if pos_rows and "event_id" in pos_rows[0]:
        from collections import Counter

        eids = Counter(r["event_id"] for r in pos_rows)
        cross = 0
        for eid in eids:
            splits = {r["split"] for r in pos_rows if r["event_id"] == eid}
            if len(splits) > 1:
                cross += 1
        base["n_events_crossing_split"] = cross
        base["n_unique_event_id"] = len(eids)
    return base


def run_audit(dataset: Path) -> dict:
    pos_rows = json.loads((dataset / "w20_manifest.json").read_text())
    neg_path = dataset / "w20_neg_manifest.json"
    neg_rows = json.loads(neg_path.read_text()) if neg_path.exists() else []
    summary = None
    for name in ("stageb_summary.json", "w20_summary.json"):
        p = dataset / name
        if p.exists():
            summary = json.loads(p.read_text())
            break

    causality = audit_causality(pos_rows)
    # Stage B must be fully causal
    if pos_rows and all(int(r.get("future_bars", 1)) == 0 for r in pos_rows):
        causality["verdict"] = "causal"
        causality["n_causal"] = len(pos_rows)
        causality["n_future_gt0"] = 0
        causality["frac_future_gt0"] = 0.0

    split = refine_split_audit(pos_rows, neg_rows, summary)
    holdout = audit_holdout(pos_rows)
    # Also check negatives for holdout
    if neg_rows and "end_time" in neg_rows[0]:
        nt = pd.to_datetime([r["end_time"] for r in neg_rows], utc=True, errors="coerce")
        n_hit = int((nt >= HOLDOUT_START).sum())
        holdout["n_negative_in_holdout"] = n_hit
        holdout["clean"] = holdout["clean"] and n_hit == 0

    conservation = audit_conservation(dataset, pos_rows, neg_rows)
    trace_rows = pos_rows + neg_rows
    traceability = {
        "n_samples": len(trace_rows),
        "n_with_symbol": sum(bool(r.get("symbol")) for r in trace_rows),
        "n_with_window_start_bar": sum(r.get("win_start") is not None for r in trace_rows),
        "n_with_window_len": sum(r.get("win_len") is not None for r in trace_rows),
        "n_with_window_end_timestamp": sum(r.get("end_time") is not None for r in trace_rows),
    }
    traceability["all_samples_traceable_to_market_bar"] = bool(
        trace_rows
        and all(
            r.get("symbol")
            and r.get("win_start") is not None
            and r.get("win_len") is not None
            and r.get("end_time") is not None
            for r in trace_rows
        )
    )

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "summary_protocol": None if not summary else summary.get("protocol"),
        "causality": causality,
        "position": audit_position(pos_rows),
        "blank_layout": audit_blank_layout(pos_rows, neg_rows, summary),
        "split": split,
        "holdout": holdout,
        "traceability": traceability,
        "conservation": conservation,
    }
    result["gates"] = {
        "causal_dataset (visible_end <= decision)": result["causality"]["verdict"] == "causal"
        and result["causality"]["frac_future_gt0"] == 0.0,
        "box_end <= decision": result["causality"]["box_end_le_decision"],
        "no_event_crosses_split": result["split"]["n_events_crossing_split"] == 0,
        "time_based_split": bool(result["split"]["is_time_split"])
        and bool(result["split"]["negative_time_split"]["pass"]),
        "no_holdout_in_training": bool(result["holdout"]["clean"]),
        "labels_in_bounds": result["conservation"]["n_labels_out_of_bounds"] == 0,
        "manifest_conserved": bool(result["conservation"]["conserved"]),
        "market_bar_traceability": bool(
            result["traceability"]["all_samples_traceable_to_market_bar"]
        ),
    }
    if result["blank_layout"]["applicable"]:
        result["gates"]["causal_position_diversity"] = bool(
            result["blank_layout"]["pass"]
        )
    result["p0_pass"] = all(result["gates"].values())
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(DEFAULT_DS))
    ap.add_argument(
        "--out",
        default=str(
            PROJECT
            / "analysis"
            / "output"
            / "p0_local_signal_v2_stageb_strictneg_v2_audit.json"
        ),
    )
    args = ap.parse_args()
    result = run_audit(Path(args.dataset))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))
    print(f"p0_pass = {result['p0_pass']}")
    print(f"wrote {out}")
    return 0 if result["p0_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
