#!/usr/bin/env python3
"""Measure how much each 15m review candidate moved before its anchor bar.

For every PENDING candidate this audit reads only ``open/high/low/close/volume``
from the bounded pre-holdout source prefix.  It compares ``open[t]`` with
``close[t-3]``, ``close[t-6]`` and ``close[t-12]`` in the candidate direction,
normalized by the already-recorded Pine-RMA ATR14 at ``t``.  It also measures
the signed body of completed anchor bar ``t``.  No future row, label, outcome,
model score, holdout OHLCV or existing Owner-label manifest is used.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    CandidateCollectionError,
    read_preholdout_prefix,
    sha256_file,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-candidate1000-v1"
    / "results"
)
DEFAULT_MANIFEST = DEFAULT_RESULTS / "review_manifest.jsonl"
DEFAULT_OUTPUT = DEFAULT_RESULTS / "prelaunch_audit.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_auditor_committed() -> str:
    """Require this auditor to exist unchanged on main before writing evidence."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("prelaunch auditor must run on main")
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    if git_output("status", "--short", "--", relative):
        raise RuntimeError("prelaunch auditor is not committed")
    commit = git_output("log", "-1", "--format=%H", "--", relative)
    if len(commit) != 40:
        raise RuntimeError("could not resolve auditor commit")
    return commit


def repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise CandidateCollectionError(f"source path escapes repository: {value}") from exc
    return path


def prelaunch_metrics(
    frame: pd.DataFrame,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Return causal pre-anchor displacement fields for one public candidate.

    Source columns are ``close`` at ``t-3/t-6/t-12`` and ``open/close`` at
    ``t``.  The fixed normalizer is the candidate manifest's ATR14 at ``t``.
    The longest window ends at ``t`` and uses no later row.
    """

    source_i = int(row["source_anchor_i"])
    direction = 1 if row["direction"] == "LONG" else -1
    atr = float(row["atr14_signal"])
    anchor_open = float(row["anchor_open"])
    if atr <= 0.0:
        raise CandidateCollectionError(f"non-positive ATR: {row['event_id']}")
    lookup = frame.set_index("_source_i")
    required = [source_i, source_i - 3, source_i - 6, source_i - 12]
    if any(index not in lookup.index for index in required):
        raise CandidateCollectionError(f"prelaunch history crosses a gap: {row['event_id']}")
    result = {
        "event_id": str(row["event_id"]),
        "direction": str(row["direction"]),
        "symbol": str(row["symbol"]),
        "rank": int(row["rank"]),
        "anchor_time": str(row["anchor_time"]),
    }
    for lag in (3, 6, 12):
        previous_close = float(lookup.loc[source_i - lag, "close"])
        result[f"pre{lag}_open_signed_atr"] = (
            direction * (anchor_open - previous_close) / atr
        )
    anchor_close = float(lookup.loc[source_i, "close"])
    result["anchor_body_signed_atr"] = direction * (anchor_close - anchor_open) / atr
    return result


def summarize(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_total: int = 1000,
    expected_per_side: int = 500,
) -> dict[str, Any]:
    """Summarize all rows by direction with explicit counts and rates."""

    frame = pd.DataFrame(list(rows))
    if len(frame) != expected_total:
        raise CandidateCollectionError(
            f"prelaunch audit expected {expected_total} rows, got {len(frame)}"
        )
    output: dict[str, Any] = {"rows": len(frame), "sides": {}}
    for side, group in frame.groupby("direction", sort=True):
        if len(group) != expected_per_side:
            raise CandidateCollectionError(
                f"{side} expected {expected_per_side} rows, got {len(group)}"
            )
        side_result: dict[str, Any] = {"rows": len(group)}
        for lag in (3, 6, 12):
            field = f"pre{lag}_open_signed_atr"
            above_one = int(group[field].gt(1.0).sum())
            above_two = int(group[field].gt(2.0).sum())
            side_result.update(
                {
                    f"pre{lag}_median_signed_atr": float(group[field].median()),
                    f"pre{lag}_gt_1_atr": above_one,
                    f"pre{lag}_gt_1_atr_rate": above_one / len(group),
                    f"pre{lag}_gt_2_atr": above_two,
                    f"pre{lag}_gt_2_atr_rate": above_two / len(group),
                }
            )
        body_above_one = int(group["anchor_body_signed_atr"].gt(1.0).sum())
        side_result.update(
            {
                "anchor_body_median_signed_atr": float(
                    group["anchor_body_signed_atr"].median()
                ),
                "anchor_body_gt_1_atr": body_above_one,
                "anchor_body_gt_1_atr_rate": body_above_one / len(group),
            }
        )
        output["sides"][str(side)] = side_result
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-total", type=int, default=1000)
    parser.add_argument("--expected-per-side", type=int, default=500)
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    output = args.out.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite audit: {output}")
    auditor_commit = verify_auditor_committed()
    manifest_rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest_rows:
        grouped[str(row["source_path"])].append(row)

    audited: list[dict[str, Any]] = []
    boundary_timestamps = 0
    for source, rows in sorted(grouped.items()):
        frame, source_audit = read_preholdout_prefix(
            repo_path(source), end_exclusive=HOLDOUT_START
        )
        if int(source_audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("prelaunch audit materialized holdout OHLCV")
        boundary_timestamps += int(source_audit["boundary_timestamp_rows_inspected"])
        for row in rows:
            audited.append(prelaunch_metrics(frame, row))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auditor_commit": auditor_commit,
        "manifest_path": str(manifest.relative_to(ROOT)),
        "manifest_sha256": sha256_file(manifest),
        "definition": {
            "pre_lag_open_signed_atr": "direction * (open[t] - close[t-lag]) / ATR14[t]",
            "anchor_body_signed_atr": "direction * (close[t] - open[t]) / ATR14[t]",
            "causal_rows_used": "t-12 through completed t only",
        },
        "holdout": {
            "start_exclusive": HOLDOUT_START.isoformat(),
            "ohlcv_rows_materialized": 0,
            "boundary_timestamp_rows_inspected": boundary_timestamps,
            "read": False,
        },
        "summary": summarize(
            audited,
            expected_total=args.expected_total,
            expected_per_side=args.expected_per_side,
        ),
        "rows": sorted(audited, key=lambda row: (row["direction"], row["rank"])),
    }
    write_json(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
