"""Compare the historical 5m outcome datasets with the causal-v2 rebuild.

This audit reads manifests, receipts, and training/economic summaries only. It
does not open market data, model weights, or holdout rows. The old manifests do
not persist ``decision_i``; their frozen preregistration declares entry at
``core_end + 2``, so ``post_bars > 2`` is the exact visible-future predicate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OLD_ONE = ROOT / "datasets/ma_launch_5m_outcome_v1"
OLD_EIGHT = ROOT / "datasets/ma_launch_5m_outcome_x8_v1"
NEW = ROOT / "datasets/ma_launch_5m_outcome_causal_v2"
OLD_ECONOMIC = ROOT / "analysis/output/detector_net_returns_20260830/5m_outcome_summary.json"
OLD_CURVE = (
    ROOT
    / "analysis/output/ma_launch_5m_outcome_v1/"
    "ma_launch_5m_outcome_v1_y11s_ft960/results.csv"
)
DEFAULT_OUT = (
    ROOT
    / "experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/old_new_comparison.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def load_manifest(directory: Path) -> list[dict[str, object]]:
    path = directory / "manifest.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    """Summarize independent events, visible future and split isolation."""
    events = Counter(str(row["event_id"]) for row in rows)
    image_hashes = Counter(str(row["image_sha256"]) for row in rows)
    if rows and "decision_i" in rows[0]:
        future = [
            row for row in rows if int(row["window_end_i"]) > int(row["decision_i"])
        ]
    else:
        future = [row for row in rows if int(row["post_bars"]) > 2]
    split_events: dict[str, set[str]] = {"train": set(), "val": set()}
    for row in rows:
        split_events[str(row["split"])].add(str(row["event_id"]))
    return {
        "rows": len(rows),
        "unique_events": len(events),
        "mean_rows_per_event": len(rows) / len(events),
        "min_rows_per_event": min(events.values()),
        "max_rows_per_event": max(events.values()),
        "events_with_multiple_rows": sum(count > 1 for count in events.values()),
        "rows_from_multirow_events": sum(count for count in events.values() if count > 1),
        "visible_future_rows": len(future),
        "visible_future_rate": len(future) / len(rows),
        "duplicate_image_hash_groups": sum(count > 1 for count in image_hashes.values()),
        "event_split_overlap": len(split_events["train"] & split_events["val"]),
        "sample_kinds": dict(sorted(Counter(str(row["sample_kind"]) for row in rows).items())),
        "splits": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-one", type=Path, default=OLD_ONE)
    parser.add_argument("--old-eight", type=Path, default=OLD_EIGHT)
    parser.add_argument("--new", type=Path, default=NEW)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    directories = {
        "historical_one_view": args.old_one.resolve(),
        "historical_eight_view": args.old_eight.resolve(),
        "causal_v2": args.new.resolve(),
    }
    rows = {name: load_manifest(path) for name, path in directories.items()}
    summaries = {name: summarize(values) for name, values in rows.items()}

    old_labels = {
        str(row["event_id"]): str(row["sample_kind"])
        for row in rows["historical_one_view"]
    }
    new_labels = {
        str(row["event_id"]): str(row["sample_kind"])
        for row in rows["causal_v2"]
    }
    overlap = sorted(set(old_labels) & set(new_labels))
    changed = [event_id for event_id in overlap if old_labels[event_id] != new_labels[event_id]]

    curve = pd.read_csv(OLD_CURVE)
    curve.columns = [column.strip() for column in curve.columns]
    map50_column = "metrics/mAP50(B)"
    map5095_column = "metrics/mAP50-95(B)"
    best_map50_i = int(curve[map50_column].idxmax())
    best_map5095_i = int(curve[map5095_column].idxmax())
    economic = json.loads(OLD_ECONOMIC.read_text())

    receipt = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_commit": git_head(),
        "generator_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "manifests": {
            name: {
                "path": (path / "manifest.jsonl").relative_to(ROOT).as_posix(),
                "sha256": sha256_file(path / "manifest.jsonl"),
                **summaries[name],
            }
            for name, path in directories.items()
        },
        "overlapping_one_view_events": len(overlap),
        "changed_outcome_labels_after_timing_repair": len(changed),
        "changed_outcome_label_rate": len(changed) / len(overlap),
        "changed_outcome_directions": dict(
            sorted(
                Counter(
                    f"{old_labels[event]}->{new_labels[event]}" for event in changed
                ).items()
            )
        ),
        "historical_one_view_training": {
            "curve_path": OLD_CURVE.relative_to(ROOT).as_posix(),
            "curve_sha256": sha256_file(OLD_CURVE),
            "best_map50": float(curve.loc[best_map50_i, map50_column]),
            "best_map50_epoch": int(curve.loc[best_map50_i, "epoch"]),
            "best_map50_95": float(curve.loc[best_map5095_i, map5095_column]),
            "best_map50_95_epoch": int(curve.loc[best_map5095_i, "epoch"]),
        },
        "historical_one_view_economic_evaluation": economic,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "production_eligible": False,
    }
    out_path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
