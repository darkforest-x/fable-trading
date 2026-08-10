#!/usr/bin/env python3
"""Apply the frozen P1 discovery gate to A/B1/B2/C3 event-eval JSONs."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = PROJECT / "analysis" / "output" / "p1_local_signal_v2"


def select_best_f1(rows: list[dict]) -> dict:
    return max(rows, key=lambda row: (row["event_f1"], row["event_precision"]))


def select_gate_point(rows: list[dict], gate: dict) -> dict | None:
    eligible = [
        row
        for row in rows
        if row["event_recall"] >= gate["event_recall_min"]
        and row["event_precision"] >= gate["event_precision_min"]
        and row["fp_per_1000_bars"] <= gate["fp_per_1000_bars_max"]
    ]
    if not eligible:
        return None
    # Primary objective is silence at the frozen recall/precision floor.
    return min(
        eligible,
        key=lambda row: (
            row["fp_per_1000_bars"],
            -row["event_precision"],
            -row["event_recall"],
            -row["threshold"],
        ),
    )


def summarize_arm(result: dict, gate: dict) -> dict:
    rows = result["thresholds"]
    point = select_gate_point(rows, gate)
    max_recall = max(rows, key=lambda row: row["event_recall"])
    best_f1 = select_best_f1(rows)
    return {
        "arm": result["arm"],
        "weights": result["weights"],
        "discovery_gate_pass": point is not None,
        "gate_operating_point": point,
        "best_f1_point": best_f1,
        "max_recall_point": max_recall,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS / "comparison.json")
    args = parser.parse_args()
    gate_file = args.results_dir / "baseline_gate.json"
    gate_doc = json.loads(gate_file.read_text())
    gate = gate_doc["candidate_discovery_gate"]
    arms = []
    missing = []
    for arm in ("A", "B1", "B2", "C3"):
        path = args.results_dir / f"{arm}_event_eval.json"
        if not path.exists():
            missing.append(arm)
            continue
        arms.append(summarize_arm(json.loads(path.read_text()), gate))
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "arms": arms,
        "missing_arms": missing,
        "matrix_complete": not missing,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if not missing else 3


if __name__ == "__main__":
    raise SystemExit(main())
