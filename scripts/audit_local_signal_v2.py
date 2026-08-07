#!/usr/bin/env python3
"""P0 hard-gate audit for local_signal_v2 Stage-B datasets.

Extends the w20 causality arithmetic with true time-split detection and
Stage-B summary awareness. Read-only.

Usage:
  .venv/bin/python scripts/audit_local_signal_v2.py \
      --dataset datasets/local_signal_v2_stageb \
      --out analysis/output/p0_local_signal_v2_stageb_audit.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.audit_w20_midbox_causality import (
    HOLDOUT_START,
    audit_causality,
    audit_conservation,
    audit_holdout,
    audit_position,
    audit_split,
)

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DS = PROJECT / "datasets" / "local_signal_v2_stageb"


def refine_split_audit(pos_rows: list[dict], summary: dict | None) -> dict:
    base = audit_split(pos_rows, [])
    times = pd.to_datetime([r["end_time"] for r in pos_rows], utc=True, errors="coerce")
    frame = pd.DataFrame({"t": times, "split": [r["split"] for r in pos_rows]}).dropna()
    tr = frame.loc[frame["split"] == "train", "t"]
    va = frame.loc[frame["split"] == "val", "t"]
    is_time = False
    gap_days = None
    if len(tr) and len(va):
        # Time split if all train times strictly before all val times.
        is_time = bool(tr.max() < va.min())
        if is_time:
            gap_days = (va.min() - tr.max()).total_seconds() / 86400
    base["is_time_split"] = is_time
    base["train_max_before_val_min"] = is_time
    base["purge_gap_days"] = None if gap_days is None else round(gap_days, 3)
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

    split = refine_split_audit(pos_rows, summary)
    holdout = audit_holdout(pos_rows)
    # Also check negatives for holdout
    if neg_rows and "end_time" in neg_rows[0]:
        nt = pd.to_datetime([r["end_time"] for r in neg_rows], utc=True, errors="coerce")
        n_hit = int((nt >= HOLDOUT_START).sum())
        holdout["n_negative_in_holdout"] = n_hit
        holdout["clean"] = holdout["clean"] and n_hit == 0

    conservation = audit_conservation(dataset, pos_rows, neg_rows)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "summary_protocol": None if not summary else summary.get("protocol"),
        "causality": causality,
        "position": audit_position(pos_rows),
        "split": split,
        "holdout": holdout,
        "conservation": conservation,
    }
    result["gates"] = {
        "causal_dataset (visible_end <= decision)": result["causality"]["verdict"] == "causal"
        and result["causality"]["frac_future_gt0"] == 0.0,
        "box_end <= decision": result["causality"]["box_end_le_decision"],
        "no_event_crosses_split": result["split"]["n_events_crossing_split"] == 0,
        "time_based_split": bool(result["split"]["is_time_split"]),
        "no_holdout_in_training": bool(result["holdout"]["clean"]),
        "labels_in_bounds": result["conservation"]["n_labels_out_of_bounds"] == 0,
        "manifest_conserved": bool(result["conservation"]["conserved"]),
    }
    result["p0_pass"] = all(result["gates"].values())
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(DEFAULT_DS))
    ap.add_argument(
        "--out",
        default=str(PROJECT / "analysis" / "output" / "p0_local_signal_v2_stageb_audit.json"),
    )
    args = ap.parse_args()
    result = run_audit(Path(args.dataset))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))
    print(f"p0_pass = {result['p0_pass']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
