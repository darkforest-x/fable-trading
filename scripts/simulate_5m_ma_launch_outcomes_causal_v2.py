"""Build pre-holdout 5m outcome labels under one causal close-entry contract.

For every rule-proposed MA-launch event, the model-visible decision bar is
``core_end + 2``.  Entry uses that completed bar's close; TP5/SL2 resolution
begins on the next 5-minute bar and runs for 144 bars.  The source reader is
hard-truncated before the canonical holdout boundary, so a row without its full
12-hour label horizon is dropped rather than reading across the boundary.

Columns used from market data: open_time, open, high, low, close plus causal
ATR14 derived from those columns.  Labels are future outcomes; model features
and rendered inputs are not produced here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_5m_causal import (  # noqa: E402
    BAR_MINUTES,
    CONTRACT_VERSION,
    HORIZON_BARS,
    ROUND_TRIP_COST,
    atr_series,
    net_atr_from_resolution,
    resolve_causal_trade,
    timing_from_core_end,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402

CANDIDATES = ROOT / "analysis/output/ma_launch_5m_candidates_20260830/candidates_5m.jsonl"
DEFAULT_OUT = ROOT / "analysis/output/ma_launch_5m_outcomes_causal_v2_20260831"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    """Return the committed generator revision used for this artifact."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    candidates_path = args.candidates.resolve()
    out_dir = args.out.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {out_dir}")

    rows = [json.loads(line) for line in candidates_path.read_text().splitlines() if line.strip()]
    event_ids = [str(row["event_id"]) for row in rows]
    if len(set(event_ids)) != len(event_ids):
        raise SystemExit("candidate event_id values are not unique")
    if args.limit:
        rows = rows[: args.limit]
    rows.sort(key=lambda row: (str(row["source_path"]), str(row["event_id"])))
    print(f"patterns: {len(rows)}", flush=True)

    cache: dict[str, pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    skipped: Counter[str] = Counter()
    duration = pd.Timedelta(minutes=BAR_MINUTES)
    holdout = pd.Timestamp(HOLDOUT_START)

    for number, row in enumerate(rows, 1):
        source = str(row["source_path"])
        if source not in cache:
            frame, _ = read_preholdout_prefix(
                ROOT / source,
                end_exclusive=HOLDOUT_START,
                bar_minutes=BAR_MINUTES,
            )
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            cache = {source: frame}
        frame = cache[source]

        timing = timing_from_core_end(int(row["source_core_end_i"]))
        if timing.outcome_start_i + HORIZON_BARS > len(frame):
            skipped["full pre-holdout horizon unavailable"] += 1
            continue
        atr = float(frame["atr14"].iloc[timing.decision_i])
        price = float(frame["close"].iloc[timing.decision_i])
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(price) or price <= 0:
            skipped["invalid decision ATR or close"] += 1
            continue

        decision_open = pd.Timestamp(frame["open_time"].iloc[timing.decision_i])
        decision_at = decision_open + duration
        outcome_start_at = pd.Timestamp(frame["open_time"].iloc[timing.outcome_start_i])
        horizon_end_at = decision_at + HORIZON_BARS * duration
        if decision_at != outcome_start_at:
            skipped["non-contiguous decision/outcome boundary"] += 1
            continue
        if horizon_end_at > holdout:
            skipped["label horizon reaches holdout"] += 1
            continue

        side = str(row["direction"]).upper()
        try:
            resolution = resolve_causal_trade(
                frame,
                decision_i=timing.decision_i,
                side=side,
                horizon_bars=HORIZON_BARS,
            )
            net_atr = net_atr_from_resolution(resolution, entry_atr=atr)
        except (IndexError, ValueError) as exc:
            skipped[f"unresolvable: {type(exc).__name__}"] += 1
            continue

        records.append(
            {
                "event_id": str(row["event_id"]),
                "symbol": str(row["symbol"]),
                "direction": side,
                "source_path": source,
                "core_start_i": int(row["source_core_start_i"]),
                "core_end_i": timing.core_end_i,
                "decision_i": timing.decision_i,
                "visible_end_i": timing.visible_end_i,
                "outcome_start_i": timing.outcome_start_i,
                "core_end_time": str(row["core_end_time"]),
                "decision_at": decision_at.isoformat(),
                "visible_end_at": decision_at.isoformat(),
                "outcome_start_at": outcome_start_at.isoformat(),
                "horizon_end_at": horizon_end_at.isoformat(),
                "entry_price_source": "decision_close",
                "entry_price": price,
                "entry_atr": atr,
                "atr_pct": atr / price,
                "horizon_bars": HORIZON_BARS,
                "barrier_outcome": resolution.outcome,
                "exit_offset_from_outcome_start": resolution.exit_offset,
                "exit_time": resolution.exit_time,
                "net_atr": net_atr,
                "round_trip_cost": ROUND_TRIP_COST,
                "outcome_contract": CONTRACT_VERSION,
                "holdout_read": False,
            }
        )
        if number % 400 == 0:
            print(f"  {number}/{len(rows)}", flush=True)

    records.sort(key=lambda row: str(row["event_id"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "outcomes.jsonl"
    csv_path = out_dir / "outcomes.csv"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )
    pd.DataFrame(records).to_csv(csv_path, index=False)

    counts = Counter(str(row["barrier_outcome"]) for row in records)
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_commit": git_head(),
        "generator_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "contract": CONTRACT_VERSION,
        "candidate_path": candidates_path.relative_to(ROOT).as_posix(),
        "candidate_sha256": sha256_file(candidates_path),
        "candidate_rows": len(rows),
        "resolved_rows": len(records),
        "outcomes": dict(sorted(counts.items())),
        "skipped": dict(sorted(skipped.items())),
        "entry_lag_bars": 2,
        "horizon_bars": HORIZON_BARS,
        "bar_minutes": BAR_MINUTES,
        "holdout_start": holdout.isoformat(),
        "holdout_rows_read": 0,
        "training_eligible": False,
        "production_eligible": False,
        "outcomes_jsonl_sha256": sha256_file(jsonl_path),
        "outcomes_csv_sha256": sha256_file(csv_path),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("\n=== causal outcomes ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
