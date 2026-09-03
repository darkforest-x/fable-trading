#!/usr/bin/env python3
"""Render the corrected scan-order overview for the finalized 5,000-event run.

The finalized month table is newest-first.  The legacy overview helper reverses
its input internally, so it must receive oldest-first rows to draw the declared
newest-first scan axis.  This repair writes a new artifact and receipt; it never
touches detections, review queues, labels, datasets, models, or trading state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import pandas as pd

from scripts import mine_15m_ma_launch_grade_a_daily_movers_5000 as mine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = mine.DEFAULT_OUT


def legacy_overview_input_order(
    month_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return oldest-first rows required by the legacy reversing helper."""

    rows = [dict(row) for row in month_rows]
    ordered = sorted(rows, key=lambda row: str(row["month"]))
    if len({str(row["month"]) for row in ordered}) != len(ordered):
        raise ValueError("month rows must be unique")
    return ordered


def render(out: Path) -> dict[str, Any]:
    """Build the corrected overview and a source-bound repair receipt."""

    summary_path = out / "summary.json"
    months_path = out / "month_summaries.csv"
    if not summary_path.is_file() or not months_path.is_file():
        raise FileNotFoundError(
            "finalized summary and month_summaries.csv are required"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("experiment_id") != mine.EXPERIMENT_ID:
        raise ValueError("summary belongs to a different experiment")
    rows = pd.read_csv(months_path).to_dict("records")
    expected_months = list(map(str, summary["months_newest_first"]))
    if [str(row["month"]) for row in rows] != expected_months:
        raise ValueError(
            "month_summaries.csv is not the declared newest-first sequence"
        )
    image = mine.build_overview(
        legacy_overview_input_order(rows),
        summary["counts"],
    )
    target = out / "overview_scan_order_corrected.png"
    if not cv2.imwrite(str(target), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
        raise OSError(f"could not write {target}")
    receipt = {
        "schema_version": 1,
        "experiment_id": mine.EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "renderer_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "summary_sha256": mine.sha256_file(summary_path),
        "month_summaries_sha256": mine.sha256_file(months_path),
        "original_overview_sha256": mine.sha256_file(out / "overview.png"),
        "corrected_overview_sha256": mine.sha256_file(target),
        "axis_order": "newest month to oldest month (scan order)",
        "months": len(rows),
        "first_axis_month": expected_months[0],
        "last_axis_month": expected_months[-1],
        "first_axis_cumulative_novel": int(rows[0]["global_novel_after_month"]),
        "last_axis_cumulative_novel": int(rows[-1]["global_novel_after_month"]),
        "detections_or_review_queue_changed": False,
        "labels_or_dataset_changed": False,
        "model_or_trading_state_changed": False,
    }
    mine.write_json(out / "overview_scan_order_corrected_receipt.json", receipt)
    return receipt


def main() -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    receipt = render(args.out.resolve())
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
