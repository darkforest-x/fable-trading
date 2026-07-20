"""Route-C covariate-shift probe: rescan a val-window slice with the v12 detector.

Owner-approved 2026-07-20: quantify "detector v12 x judgment v11 frozen" score
shift on the val window WITHOUT a full-history rescan (and without touching
holdout). Per swap series we keep only bars with open_time < 2026-05-04 (the
frozen HOLDOUT_START) and take the last 3200 of them (~33 days), so effective
candidates land roughly in 2026-04-03 .. 2026-05-03 — a sub-slice of the v11
val split (2026-03-12 .. 2026-05-03).

Everything downstream of the candidate source is byte-identical to
scripts/yolo_candidate_source.py: same add_indicators/add_features, same
tp5/sl2 labeling, same full-mode causal scan_series_with_yolo. Labels look at
most HORIZON_BARS into the future, all strictly before 2026-05-04.

Memory discipline (16GB machine, prior OOM with workers=5): single process,
sequential; run with OMP_NUM_THREADS=1 MKL_NUM_THREADS=1.

Usage: OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
    scripts/scan_v12_valwin.py --out data/judgment_yolo_swap_v12_valwin.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from scripts.yolo_candidate_source import _list_swap_jobs, _process_one
from src.data.loader import load_series
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.yolo_candidates import load_yolo_model

# Mirror of src.judgment.train.HOLDOUT_START. NOT imported: train.py pulls in
# lightgbm, and importing lightgbm before the first ultralytics predict
# segfaults this venv (duplicate libomp). Scoring runs in a separate process.
HOLDOUT_START = pd.Timestamp("2026-05-04 00:00:00", tz="UTC")
TAIL_BARS = 3200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/owner_best.pt")
    ap.add_argument(
        "--out", type=Path, default=PROJECT_DIR / "data" / "judgment_yolo_swap_v12_valwin.csv"
    )
    args = ap.parse_args()
    weights = str(Path(args.weights).resolve())

    jobs = _list_swap_jobs()
    n_series = len(jobs)
    print(f"valwin rescan: {n_series} swap series, tail={TAIL_BARS} bars < {HOLDOUT_START}", flush=True)

    model = load_yolo_model(weights)
    records: list[dict] = []
    for k, (source, symbol, path_strs) in enumerate(jobs, 1):
        frame = load_series([Path(p) for p in path_strs])
        frame = frame[frame["open_time"] < HOLDOUT_START].tail(TAIL_BARS).reset_index(drop=True)
        if len(frame) < 500:
            continue
        records.extend(_process_one(source, symbol, frame, model))
        if k % 25 == 0 or k == n_series:
            print(f"  … progress {k}/{n_series} rows={len(records)}", flush=True)

    df = pd.DataFrame(records)
    if len(df):
        df = df.sort_values("signal_time").reset_index(drop=True)
        assert df["signal_time"].max() < HOLDOUT_START, "holdout leak — refuse to write"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(
        json.dumps(
            {
                "series": n_series,
                "candidates": len(df),
                "symbols": int(df["symbol"].nunique()) if len(df) else 0,
                "pos_rate": round(float(df["label"].mean()), 4) if len(df) else None,
                "time_range": [str(df["signal_time"].min()), str(df["signal_time"].max())] if len(df) else None,
                "out": str(args.out),
                "weights": weights,
                "features": list(FEATURE_COLUMNS),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
